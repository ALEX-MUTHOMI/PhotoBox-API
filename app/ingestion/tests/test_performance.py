"""
ingestion/tests/test_performance.py

THREAT MODEL COVERAGE:
  - Integer overflow / OOM from massive bulk arrays
  - DB connection pool starvation via SELECT FOR UPDATE blocking
  - Phantom Upload Quota Starvation attack
  - Reaper refunding quota for files physically present in R2

ARCHITECTURE NOTES:
  - IngestionPerformanceAndScaleTests uses TransactionTestCase because lock
    contention tests spawn real OS threads that each hold a live DB connection.
    Django's standard TestCase wraps everything in a single transaction shared
    across threads, making SELECT FOR UPDATE deadlock instead of conflict.
  - ReaperSecurityTests uses TestCase (faster) — no cross-thread transactions.
  - All boto3 I/O is mocked at the view/task boundary. No real AWS calls.
  - R2_TEST_SETTINGS are applied at the class level so every method inherits
    them, even if a per-method patch fails or is forgotten.
"""

import time
import threading
from unittest.mock import patch, MagicMock
from botocore.exceptions import ClientError

from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.test import APIClient

from gallery.models import Workspace, Event, Scene, MediaAsset
from ingestion.tasks import reap_abandoned_uploads

User = get_user_model()


# ---------------------------------------------------------------------------
# Shared dummy R2 credentials — satisfy apps.py startup guard without
# touching real Cloudflare infrastructure.
# ---------------------------------------------------------------------------
R2_TEST_SETTINGS = dict(
    CLOUDFLARE_R2_BUCKET_NAME='test-bucket',
    CLOUDFLARE_ACCESS_KEY_ID='test-key-id',
    CLOUDFLARE_SECRET_ACCESS_KEY='test-secret-key',
    CLOUDFLARE_R2_ENDPOINT='https://test.r2.cloudflarestorage.com',
)

# Standard presigned POST response that boto3 would return.
_PRESIGNED_POST_RESPONSE = {
    'url': 'https://test-bucket.r2.cloudflarestorage.com/',
    'fields': {'x-amz-signature': 'abc123', 'key': 'test/key'},
}


# ===========================================================================
# SCALE & CONCURRENCY TESTS
# ===========================================================================

@override_settings(**R2_TEST_SETTINGS)
class IngestionPerformanceAndScaleTests(TransactionTestCase):
    """
    Validates the ingestion pipeline against extreme scale and concurrency.

    Uses TransactionTestCase because:
      1. Lock-contention tests spawn OS threads, each with their own DB
         connection.  Django's TestCase wraps everything in one shared
         transaction — cross-thread SELECT FOR UPDATE would deadlock, not
         conflict, giving a false result.
      2. TransactionTestCase commits and rolls back real transactions, so
         lock state is visible across connections exactly as in production.

    Trade-off: TransactionTestCase is slower (flushes the DB between tests)
    and does NOT wrap in a savepoint.  Keep setUp() lean.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='perf@test.com',
            password='password123',
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name='Perf Studios',
            # 50 GB — enough for normal tests; individual tests may override.
            storage_limit_bytes=50 * 1024 ** 3,
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title='Scale Event',
            slug='scale',
        )
        self.scene = Scene.objects.create(
            event=self.event,
            title='Day 1',
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        self.url = reverse('bulk-ingest')

    # -----------------------------------------------------------------------
    # AUDIT FINDING #1 — Scale Test
    #
    # ORIGINAL BUGS:
    #   a) workspace.save() after changing storage_limit_bytes would trigger
    #      any pre-save signals and overwrite fields touched by concurrent
    #      logic. QuerySet.update() is the correct atomic pattern.
    #   b) No assertion that boto3 was called exactly once per file — an N+1
    #      client instantiation bug (get_r2_client inside the loop) would
    #      pass the original test but hammer the connection pool in production.
    #   c) No assertion on quota_used_bytes — the original test only checked
    #      the count of MediaAsset rows, not that the accounting was correct.
    #
    # SECURITY THREAT: An integer overflow in quota accounting for
    # 150 × 4 GB = 600 GB could allow unbounded free storage.
    # -----------------------------------------------------------------------
    @patch('ingestion.views.generate_r2_presigned_post')
    def test_4k_video_bulk_limits(self, mock_get_r2):
        """
        THE THREAT: Integer overflows or mass array OOM from huge payloads.

        Proves the system handles 150 maximal 4K video presigned URLs without
        OOM, integer overflow, or incorrect quota accounting.

        CONTRACT:
          - 202 Accepted
          - Exactly 150 presigned tickets returned
          - Exactly 150 MediaAsset rows created in DB
          - Quota ledger debited by exactly 150 × 4 GB atomically
          - storage helper called exactly once per file
        """
        mock_get_r2.return_value = {
            'upload_url': 'https://test.r2.cloudflarestorage.com/test-bucket',
            'post_url': 'https://test.r2.cloudflarestorage.com/test-bucket',
            'post_fields': {
                'key': 'test/key',
                'x-amz-signature': 'abc123',
            },
        }

        # Give workspace enough headroom: 150 × 4 GB = 600 GB
        Workspace.objects.filter(pk=self.workspace.pk).update(
            storage_limit_bytes=1_000 * 1024 ** 3,  # 1 TB — atomic, no signals
        )
        self.workspace.refresh_from_db()

        files_payload = [
            {
                'filename': f'4k_wedding_cam_{i:03d}.mp4',
                'file_size': 4 * 1024 ** 3,           # exactly 4 GiB
                'client_reference_id': f'ref-4k-{i}',
            }
            for i in range(150)
        ]
        payload = {'scene_id': str(self.scene.pk), 'files': files_payload}

        res = self.client.post(self.url, payload, format='json')

        # --- HTTP contract ---
        self.assertEqual(
            res.status_code, status.HTTP_202_ACCEPTED,
            f'Expected 202 Accepted, got {res.status_code}: {res.data}',
        )
        self.assertEqual(
            len(res.data['upload_tickets']),
            150,
            'Must return exactly one presigned ticket per file.',
        )

        # --- DB state ---
        created_count = MediaAsset.objects.filter(scene=self.scene).count()
        self.assertEqual(
            created_count,
            150,
            f'Expected 150 MediaAsset rows, found {created_count}.',
        )

        # --- Quota ledger integrity (catches integer overflow) ---
        self.workspace.refresh_from_db()
        expected_bytes = 150 * 4 * 1024 ** 3
        self.assertEqual(
            self.workspace.storage_used_bytes,
            expected_bytes,
            f'Quota ledger mismatch. '
            f'Expected {expected_bytes:,} bytes, '
            f'got {self.workspace.storage_used_bytes:,} bytes.',
        )

        # --- One helper call per file, no dropped tickets ---
        self.assertEqual(
            mock_get_r2.call_count,
            150,
            'generate_presigned_post must be called exactly once per file.',
        )

        # --- All returned tickets have the expected shape ---
        for ticket in res.data['upload_tickets']:
            self.assertIn('upload_url', ticket)
            self.assertIn('post_url', ticket)
            self.assertIn('post_fields', ticket)
            self.assertIn('upload_id', ticket)

    # -----------------------------------------------------------------------
    # AUDIT FINDING #2 — Lock Contention Test
    #
    # ORIGINAL BUGS:
    #   a) time.sleep(0.5) to "let the thread acquire the lock" is a race
    #      condition. On a loaded CI machine, 500 ms is not guaranteed.
    #      Fixed with a threading.Event() as a synchronisation primitive.
    #   b) The background thread did NOT close its DB connection after the
    #      transaction completed. Django does not auto-close connections for
    #      non-request threads. Leaked connections exhaust the pool and corrupt
    #      subsequent tests. Fixed with connections.close_all() in finally.
    #   c) The original test body referenced an undefined `payload` variable
    #      inside the commented-out code — it would raise NameError at runtime.
    #   d) t.join() was only called in the happy path. If the assertion failed,
    #      the thread kept running into teardown. Fixed with try/finally.
    #   e) The assertion checked for a hardcoded string from res.data — brittle
    #      against message wording changes. Now checks for presence of a
    #      meaningful keyword ("bulk upload" or "retry") in the response.
    #
    # SECURITY THREAT: If the view blocks instead of returning 409, a single
    # slow request can hold a DB connection open, starving the connection pool
    # and causing a de-facto DoS against all concurrent users of the workspace.
    # -----------------------------------------------------------------------
    @patch('ingestion.views.generate_r2_presigned_post')
    def test_db_lock_contention_survival(self, mock_get_r2):
        """
        THE THREAT: A slow external call (boto3/Cloudflare) holds a
        SELECT FOR UPDATE on the workspace row.  A second in-flight upload
        from the same workspace must get 409 Conflict immediately — never
        block, never return 500, never exhaust the connection pool.

        Proves select_for_update(nowait=True) + OperationalError → 409.
        """
        mock_client = MagicMock()
        mock_client.generate_presigned_post.return_value = _PRESIGNED_POST_RESPONSE
        mock_get_r2.return_value = mock_client

        payload = {
            'scene_id': str(self.scene.pk),
            'files': [
                {
                    'filename': 'img.jpg',
                    'file_size': 1024,
                    'client_reference_id': 'ref-contention-1',
                }
            ],
        }

        # Synchronisation: the HTTP request must NOT fire until the DB lock
        # is confirmed held by the background thread.
        lock_acquired = threading.Event()

        def hold_workspace_lock():
            """
            Acquires a real SELECT FOR UPDATE on the workspace row and holds
            it for 3 seconds, simulating a slow boto3 API call mid-transaction.

            CRITICAL: Always closes the DB connection in `finally`.
            Django does not manage connections for non-request threads.
            Failing to close here leaks connections and corrupts the pool
            for every subsequent test in the run.
            """
            try:
                with transaction.atomic():
                    Workspace.objects.select_for_update(nowait=False).get(
                        pk=self.workspace.pk,
                    )
                    lock_acquired.set()   # ← deterministic signal, no sleep()
                    time.sleep(3)         # hold the lock (simulates slow boto3)
            except Exception:
                lock_acquired.set()       # unblock main thread even on failure
            finally:
                connections.close_all()   # ← mandatory: prevent pool exhaustion

        t = threading.Thread(target=hold_workspace_lock, daemon=True)
        t.start()

        # Block until the background thread signals it holds the lock.
        # 5 s timeout prevents the test from hanging forever on CI failure.
        acquired = lock_acquired.wait(timeout=5)
        self.assertTrue(
            acquired,
            'Background thread failed to acquire the workspace lock within 5 s. '
            'This is a test infrastructure failure, not an application failure.',
        )

        try:
            # Fire the upload request while the lock is held.
            # The view MUST detect contention via nowait=True and return 409
            # immediately without blocking.
            res = self.client.post(self.url, payload, format='json')

            self.assertEqual(
                res.status_code,
                status.HTTP_409_CONFLICT,
                f'Expected 409 Conflict under lock contention, got {res.status_code}. '
                'Ensure the view calls select_for_update(nowait=True) and '
                'catches django.db.OperationalError to return HTTP 409.',
            )

            # Response body must give the client enough context to retry.
            response_text = str(res.data).lower()
            self.assertTrue(
                'bulk upload' in response_text or 'retry' in response_text,
                f'409 response must describe the contention to aid retry logic. '
                f'Got: {res.data}',
            )
        finally:
            # Always join — even on assertion failure — so the lock is
            # released before DB teardown begins.
            t.join(timeout=5)


# ===========================================================================
# REAPER / PHANTOM UPLOAD TESTS
# ===========================================================================

@override_settings(**R2_TEST_SETTINGS)
class ReaperSecurityTests(TestCase):
    """
    Defends against Phantom Upload Quota Starvation.

    Attack vector:
      1. Hacker negotiates a presigned POST for a 4 GB file.
      2. They upload directly to R2 (quota debited).
      3. They suppress the webhook so the asset stays PENDING.
      4. The Reaper runs, sees a stale PENDING asset, refunds quota.
      5. Repeat indefinitely for unlimited free storage.

    The defence: before refunding, the Reaper calls head_object.
    If the file is physically present in R2, QUARANTINE it — never refund.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='hacker@test.com',
            password='password123',
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            storage_used_bytes=5 * 1024 ** 3,   # 5 GiB already charged
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title='Phantom Event',
            slug='phantom',
        )
        self.scene = Scene.objects.create(
            event=self.event,
            title='Phantom Scene',
        )

        # --- Hacker's asset: physically exists in R2, webhook suppressed ---
        self.phantom_asset = MediaAsset.objects.create(
            scene=self.scene,
            file_size_bytes=4 * 1024 ** 3,   # 4 GiB
            status='PENDING',
            r2_object_key='hacker/phantom_file.mp4',
        )
        # Use QuerySet.update() to bypass auto_now field protection.
        # The Reaper must consider assets older than 24 h as stale.
        MediaAsset.objects.filter(pk=self.phantom_asset.pk).update(
            uploaded_at=timezone.now() - timedelta(hours=48),
        )

        # --- Legitimate orphan: client crashed before file reached R2 ---
        self.legit_orphan = MediaAsset.objects.create(
            scene=self.scene,
            file_size_bytes=1024,
            status='PENDING',
            r2_object_key='legit/orphan_file.jpg',
        )
        MediaAsset.objects.filter(pk=self.legit_orphan.pk).update(
            uploaded_at=timezone.now() - timedelta(hours=48),
        )

    # -----------------------------------------------------------------------
    # AUDIT FINDING #3 — Reaper Test
    #
    # ORIGINAL BUGS:
    #   a) self.phantom_asset.uploaded_at = ... / .save() DOES NOT WORK for
    #      auto_now fields. Django silently ignores the assignment. The asset
    #      would have a fresh timestamp and the Reaper would skip it entirely,
    #      making the test vacuously pass. Fixed with QuerySet.update().
    #   b) The assertion checked a string return value from reap_abandoned_uploads()
    #      ("Reaped: 1. Phantoms Caught: 1.") — coupling the test to an internal
    #      log string. The contract should be verified through observable state:
    #      DB row statuses and quota balance. String return values are not a
    #      reliable contract for a Celery task.
    #   c) No assertion that head_object was called for BOTH assets. Without
    #      this, a naive "skip all PENDING assets" implementation would pass.
    #   d) No assertion that the phantom asset's quota was NOT refunded — only
    #      that the total workspace bytes matched an expected value. If the
    #      phantom's 4 GB was refunded and then recharged for another reason,
    #      the total could still match. Explicit delta check added.
    #
    # SECURITY THREAT: If the Reaper refunds quota for files physically present
    # in R2, a hacker gets unlimited free storage via the phantom upload loop.
    # -----------------------------------------------------------------------
    @patch('gallery.storage.get_r2_client')
    def test_phantom_upload_reaper_defense(self, mock_get_r2):
        """
        THE THREAT: Hacker uploads 4 GiB directly to R2, suppresses the
        webhook, and waits for the Reaper to refund quota.

        THE TEST: Proves head_object blocks the refund.
          - Phantom asset (file present in R2) → QUARANTINED, quota NOT refunded
          - Legitimate orphan (file absent from R2) → FAILED, quota refunded
          - Workspace quota delta = exactly −1024 bytes (legit file only)
          - head_object called for EVERY pending asset, no skips
        """
        mock_client = MagicMock()

        def selective_head_object(Bucket, Key):
            """
            Simulates R2 state:
              - hacker's file: physically present → return metadata
              - legit orphan:  never reached R2  → raise 404 ClientError
            """
            if Key == 'hacker/phantom_file.mp4':
                return {'ContentLength': 4 * 1024 ** 3}
            raise ClientError(
                {'Error': {'Code': '404', 'Message': 'Not Found'}},
                'HeadObject',
            )

        mock_client.head_object.side_effect = selective_head_object
        mock_get_r2.return_value = mock_client

        # Record quota BEFORE reaping so delta is unambiguous.
        self.workspace.refresh_from_db()
        quota_before = self.workspace.storage_used_bytes

        # --- Execute ---
        reap_abandoned_uploads()

        # --- Phantom asset: must be QUARANTINED, not FAILED ---
        self.phantom_asset.refresh_from_db()
        self.assertEqual(
            self.phantom_asset.status,
            'QUARANTINED',
            'SECURITY FAILURE: Phantom asset was not quarantined. '
            'The file exists in R2 — refunding its quota enables the '
            'Phantom Upload Quota Starvation attack.',
        )

        # --- Legitimate orphan: must be FAILED (quota refunded) ---
        self.legit_orphan.refresh_from_db()
        self.assertEqual(
            self.legit_orphan.status,
            'FAILED',
            'Legitimate abandoned asset must be marked FAILED so the '
            "user's storage quota is correctly reclaimed.",
        )

        # --- Quota delta: exactly −1024 bytes (legit orphan only) ---
        self.workspace.refresh_from_db()
        quota_after = self.workspace.storage_used_bytes

        self.assertEqual(
            quota_after,
            quota_before - 1024,
            f'Quota ledger incorrect. '
            f'Before: {quota_before:,} bytes. '
            f'After:  {quota_after:,} bytes. '
            f'Expected delta: −1024 bytes (legit orphan only). '
            f"The phantom asset's 4 GiB MUST NOT have been refunded.",
        )

        # --- head_object called for ALL pending assets, no shortcuts ---
        self.assertEqual(
            mock_client.head_object.call_count,
            2,
            'Reaper must call head_object for every stale PENDING asset. '
            'Skipping any asset without an R2 check is a security gap.',
        )

        # --- Verify the exact keys that were checked ---
        checked_keys = {
            c.kwargs.get('Key') or c.args[1]
            for c in mock_client.head_object.call_args_list
        }
        self.assertIn(
            'hacker/phantom_file.mp4',
            checked_keys,
            'Reaper must check the phantom asset key in R2.',
        )
        self.assertIn(
            'legit/orphan_file.jpg',
            checked_keys,
            'Reaper must check the legitimate orphan key in R2.',
        )

    # -----------------------------------------------------------------------
    # BONUS TEST — Edge case the original missed entirely
    # -----------------------------------------------------------------------
    @patch('gallery.storage.get_r2_client')
    def test_reaper_skips_fresh_pending_assets(self, mock_get_r2):
        """
        THE LOGIC: Assets uploaded less than 24 hours ago are legitimately
        in-flight. The Reaper must NOT touch them, even if the webhook has
        not arrived yet.

        THE TEST: A brand-new PENDING asset (uploaded 1 hour ago) must be
        left completely untouched by the Reaper — status stays PENDING,
        quota stays unchanged, head_object never called.
        """
        mock_client = MagicMock()
        mock_get_r2.return_value = mock_client

        fresh_asset = MediaAsset.objects.create(
            scene=self.scene,
            file_size_bytes=2048,
            status='PENDING',
            r2_object_key='fresh/new_upload.jpg',
        )
        # uploaded_at defaults to now() — 1 hour ago simulates in-flight upload
        MediaAsset.objects.filter(pk=fresh_asset.pk).update(
            uploaded_at=timezone.now() - timedelta(hours=1),
        )

        self.workspace.refresh_from_db()
        quota_before = self.workspace.storage_used_bytes

        reap_abandoned_uploads()

        fresh_asset.refresh_from_db()
        self.assertEqual(
            fresh_asset.status,
            'PENDING',
            'Reaper must NOT reap fresh assets still within the upload window.',
        )

        # head_object must NOT have been called for the fresh asset
        for c in mock_client.head_object.call_args_list:
            key = c.kwargs.get('Key') or (c.args[1] if len(c.args) > 1 else '')
            self.assertNotEqual(
                key,
                'fresh/new_upload.jpg',
                'Reaper called head_object on a fresh in-flight asset — '
                'this would cause spurious quarantines for legitimate uploads.',
            )

        self.workspace.refresh_from_db()
        self.assertEqual(
            self.workspace.storage_used_bytes,
            quota_before,
            'Reaper must not alter quota for assets still within the upload window.',
        )

    @patch('gallery.storage.get_r2_client')
    def test_r2_outage_during_reaper_does_not_corrupt_quota(self, mock_get_r2):
        """
        THE CHAOS: R2 is completely down when the Reaper runs. head_object
        raises a network-level exception (not a 404 ClientError).

        THE TEST: The Reaper must handle the outage gracefully:
          - Asset remains PENDING (not incorrectly FAILED or QUARANTINED)
          - Quota is NOT refunded (the file might still exist)
          - No unhandled exception propagates out of reap_abandoned_uploads()
        """
        mock_client = MagicMock()
        mock_client.head_object.side_effect = Exception(
            'Connection refused — R2 endpoint unreachable'
        )
        mock_get_r2.return_value = mock_client

        self.workspace.refresh_from_db()
        quota_before = self.workspace.storage_used_bytes

        # Must not raise — Reaper should log the error and continue.
        try:
            reap_abandoned_uploads()
        except Exception as e:
            self.fail(
                f'reap_abandoned_uploads() raised an unhandled exception '
                f'during R2 outage: {e}'
            )

        # Both stale assets must remain PENDING — we cannot determine their
        # true state while R2 is unreachable.
        self.phantom_asset.refresh_from_db()
        self.legit_orphan.refresh_from_db()

        for asset in (self.phantom_asset, self.legit_orphan):
            self.assertNotEqual(
                asset.status,
                'FAILED',
                f'Asset {asset.pk} was incorrectly marked FAILED during R2 '
                f'outage. This could cause legitimate users to lose data.',
            )
            self.assertNotEqual(
                asset.status,
                'QUARANTINED',
                f'Asset {asset.pk} was incorrectly quarantined during R2 '
                f'outage when we had no evidence of abuse.',
            )

        self.workspace.refresh_from_db()
        self.assertEqual(
            self.workspace.storage_used_bytes,
            quota_before,
            'Quota must not be altered when R2 is unreachable during reaping.',
        )
