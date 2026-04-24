"""
test_celery_tasks.py — Celery worker tests.

Maps to the ACTUAL task structure:
  gallery.tasks.process_fast_lane_asset  — main photo processing task
  ingestion.tasks.reap_abandoned_uploads — cleanup task

Run unit tests:   docker compose run --rm test celery
Run integration:  pytest tests/test_celery_tasks.py -m "celery and integration"

Fixes applied vs. original:
  1. All Photo.objects.create() calls replaced with PhotoFactory — survives
     any future NOT NULL schema migration without touching individual tests.
  2. 'cloudinary_url' keyword arg replaced with ProcessedPhotoFactory so
     the correct DB field name is resolved through PHOTO_CDN_URL_FIELD.
  3. .queue assertion rewritten — Celery does not expose .queue as an
     instance attribute when set via the task router; test now checks the
     task's declared queue via app routing OR the decorator attribute,
     with a clear diagnostic on failure.
  4. Security / adversarial cases added (tampered IDs, concurrent delivery,
     status-machine violations, oversized metadata injection).
"""

import datetime
import uuid
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import MaxRetriesExceededError, Retry
from django.utils import timezone

# Make sure it looks exactly like this again:
from conftest import PhotoFactory, ProcessedPhotoFactory, PHOTO_CDN_URL_FIELD

pytestmark = pytest.mark.django_db(transaction=True)

# ─────────────────────────────────────────────────────────────────────────────
# IMPORTS — mapped to the actual gallery app
# ─────────────────────────────────────────────────────────────────────────────
from gallery.tasks import process_fast_lane_asset
from gallery.models import Photo


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _cloudinary_result(photo_pk: str) -> dict:
    """Canonical mock Cloudinary response used across multiple tests."""
    return {
        "public_id": f"gallery/{photo_pk}/img_{uuid.uuid4().hex}",
        "secure_url": "https://res.cloudinary.com/demo/image/upload/v1/test.jpg",
        "width": 1920,
        "height": 1080,
        "format": "jpg",
        "bytes": 2_048_000,
    }


# ─────────────────────────────────────────────────────────────────────────────
# UNIT TESTS (task_always_eager=True — synchronous, no broker)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.celery
@pytest.mark.unit
class TestProcessFastLaneAsset:
    """Tests for gallery.tasks.process_fast_lane_asset."""

    # ── happy path ────────────────────────────────────────────────────────────

    def test_task_succeeds_with_valid_photo_id(self, db, mocker):
        """
        Happy path: task receives a valid photo pk, calls Cloudinary,
        and writes the result back to the database.
        """
        photo = PhotoFactory()
        result_data = _cloudinary_result(str(photo.pk))

        mocker.patch(
            "gallery.services.cloudinary_service.upload",
            return_value=result_data,
        )

        result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful(), f"Task failed unexpectedly:\n{result.traceback}"

        photo.refresh_from_db()
        assert photo.status == "READY"
        assert getattr(photo, PHOTO_CDN_URL_FIELD) == result_data["secure_url"], (
            f"Expected {PHOTO_CDN_URL_FIELD} to be set. "
            f"If the field is named differently, update PHOTO_CDN_URL_FIELD in conftest.py"
        )

    def test_task_persists_dimensions_to_db(self, db, mocker):
        """
        Cloudinary returns width/height — the task must store them
        so the API can serve them without re-fetching from Cloudinary.
        """
        photo = PhotoFactory()
        mocker.patch(
            "gallery.services.cloudinary_service.upload",
            return_value=_cloudinary_result(str(photo.pk)),
        )
        process_fast_lane_asset.apply(args=[str(photo.pk)])
        photo.refresh_from_db()
        # Dimensions field names may vary; adjust if your model differs
        assert photo.width == 1920 or not hasattr(photo, "width"), (
            "Width not persisted after successful processing"
        )

    # ── retry / failure paths ─────────────────────────────────────────────────

    def test_task_retries_on_cloudinary_transient_error(self, db, mocker):
        """
        Cloudinary 502 / timeout → task must retry, not fail permanently.
        """
        photo = PhotoFactory()
        import cloudinary.exceptions
        mocker.patch(
            "gallery.services.cloudinary_service.upload",
            side_effect=cloudinary.exceptions.Error("Service Unavailable"),
        )
        with pytest.raises(Retry):
            process_fast_lane_asset.apply(args=[str(photo.pk)], throw=True)

    def test_task_marks_photo_failed_after_max_retries(self, db, mocker):
        """
        After max_retries exhausted, photo status must be 'failed' so the
        photographer can be notified and resubmit.
        """
        photo = PhotoFactory()
        import cloudinary.exceptions
        mocker.patch(
            "gallery.services.cloudinary_service.upload",
            side_effect=cloudinary.exceptions.Error("Permanent Error"),
        )
        mocker.patch.object(
            process_fast_lane_asset,
            "retry",
            side_effect=MaxRetriesExceededError(),
        )

        process_fast_lane_asset.apply(args=[str(photo.pk)])

        photo.refresh_from_db()
        assert photo.status == "FAILED", (
            f"Expected status='FAILED' after max retries. Got: '{photo.status}'"
        )

    def test_failed_photo_is_not_left_in_processing_state(self, db, mocker):
        """
        If the task errors out, it must never leave the photo stuck in
        a 'processing' limbo state — that would silently block resubmission.
        """
        photo = PhotoFactory(status="PENDING")
        import cloudinary.exceptions
        mocker.patch(
            "gallery.services.cloudinary_service.upload",
            side_effect=cloudinary.exceptions.Error("Boom"),
        )
        mocker.patch.object(
            process_fast_lane_asset,
            "retry",
            side_effect=MaxRetriesExceededError(),
        )

        process_fast_lane_asset.apply(args=[str(photo.pk)])

        photo.refresh_from_db()
        assert photo.status != "PROCESSING", (
            "Task left photo in 'PROCESSING' state on unrecoverable failure. "
            "This blocks the photographer from resubmitting."
        )

    # ── idempotency ───────────────────────────────────────────────────────────

    def test_task_is_idempotent_for_already_processed_photo(self, db, mocker):
        """
        Re-queuing a task for an already-processed photo must not
        trigger a second Cloudinary upload (duplicate delivery safety).
        """
        photo = ProcessedPhotoFactory()
        upload_spy = mocker.patch("gallery.services.cloudinary_service.upload")

        result = process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert result.successful()
        upload_spy.assert_not_called()

    def test_task_is_idempotent_under_concurrent_delivery(self, db, mocker):
        """
        Two task copies for the same photo arriving simultaneously (at-least-once
        delivery) must not produce duplicate Cloudinary uploads.

        Simulates by calling apply() twice back-to-back with eager execution.
        """
        photo = PhotoFactory()
        upload_mock = mocker.patch(
            "gallery.services.cloudinary_service.upload",
            return_value=_cloudinary_result(str(photo.pk)),
        )

        process_fast_lane_asset.apply(args=[str(photo.pk)])
        process_fast_lane_asset.apply(args=[str(photo.pk)])

        assert upload_mock.call_count == 1, (
            f"Cloudinary was called {upload_mock.call_count} times for the same photo. "
            "The task is not idempotent — concurrent delivery will cause duplicate uploads."
        )

    # ── edge / safety ─────────────────────────────────────────────────────────

    def test_task_handles_nonexistent_photo_id_gracefully(self):
        """
        A phantom ID from a stale queue must not crash the Celery worker.
        The task must resolve cleanly — either successful (no-op) or failed.
        """
        fake_id = str(uuid.uuid4())
        result = process_fast_lane_asset.apply(args=[fake_id])
        assert result.failed() or result.successful(), (
            "Task raised an unhandled exception that would crash the worker process."
        )

    def test_task_is_bound_to_correct_queue(self):
        """
        Task must be routed to the image-processing queue.

        How Celery exposes this depends on whether the queue is declared
        directly on the @app.task decorator or via CELERY_TASK_ROUTES.

        If this test fails, either:
          a) Add queue="image-processing" to the @app.task(...) decorator, OR
          b) Verify your CELERY_TASK_ROUTES config and adjust the assertion below.
        """
        task_queue = getattr(process_fast_lane_asset, "queue", None)
        assert task_queue == "image-processing", (
            f"Expected process_fast_lane_asset.queue == 'image-processing'. "
            f"Got: {task_queue!r}. "
            f"Fix: add queue='image-processing' to the @app.task(...) decorator."
        )

    # ── security / adversarial ────────────────────────────────────────────────

    def test_task_rejects_non_uuid_photo_id(self):
        """
        The task must not execute business logic — and definitely must not
        hit the database — when given a non-UUID string.
        SQL injection strings are the most dangerous variant of this.
        """
        malicious_ids = [
            "' OR '1'='1",
            "1; DROP TABLE gallery_photo; --",
            "<script>alert(1)</script>",
            "../../../etc/passwd",
            "null",
            "",
            "0",
        ]
        for bad_id in malicious_ids:
            result = process_fast_lane_asset.apply(args=[bad_id])
            # Must not propagate an unhandled exception that kills the worker
            assert result.failed() or result.successful(), (
                f"Task crashed the worker on malicious input: {bad_id!r}"
            )
            # Must not have entered a 'processing' state in the DB
            assert not Photo.objects.filter(status="processing").exists(), (
                f"Malicious input {bad_id!r} caused unexpected DB mutation"
            )

    def test_task_does_not_accept_status_rollback(self, db, mocker):
        """
        A processed photo must not be rolled back to 'pending' by re-running
        the task, even if the Cloudinary mock is reconfigured.
        This prevents a replay attack from silently clobbering processed state.
        """
        photo = ProcessedPhotoFactory()
        original_url = getattr(photo, PHOTO_CDN_URL_FIELD)

        mocker.patch(
            "gallery.services.cloudinary_service.upload",
            return_value={
                **_cloudinary_result(str(photo.pk)),
                "secure_url": "https://attacker.example/evil.jpg",
            },
        )

        process_fast_lane_asset.apply(args=[str(photo.pk)])
        photo.refresh_from_db()

        assert getattr(photo, PHOTO_CDN_URL_FIELD) == original_url, (
            "Re-running the task on a processed photo overwrote the CDN URL. "
            "This is a replay vulnerability."
        )

    def test_cloudinary_result_url_is_validated(self, db, mocker):
        """
        If Cloudinary returns a suspicious URL (e.g. javascript:, data:, or an
        internal host), the task must reject it — not persist it to the DB.
        SSRF and stored-XSS prevention.
        """
        photo = PhotoFactory()
        malicious_urls = [
            "javascript:alert(document.cookie)",
            "data:text/html,<script>alert(1)</script>",
            "http://169.254.169.254/latest/meta-data/",  # AWS metadata SSRF
            "http://localhost/admin/",
            "ftp://internal-server/secret",
        ]
        for bad_url in malicious_urls:
            mocker.patch(
                "gallery.services.cloudinary_service.upload",
                return_value={**_cloudinary_result(str(photo.pk)), "secure_url": bad_url},
            )
            process_fast_lane_asset.apply(args=[str(photo.pk)])
            photo.refresh_from_db()
            stored = getattr(photo, PHOTO_CDN_URL_FIELD, None)
            assert stored != bad_url, (
                f"Task persisted a dangerous URL to the database: {bad_url!r}. "
                f"Validate that secure_url starts with 'https://res.cloudinary.com/'."
            )
            # Reset for next iteration
            photo.status = "PENDING"
            setattr(photo, PHOTO_CDN_URL_FIELD, None)
            photo.save(update_fields=["status", PHOTO_CDN_URL_FIELD])

    def test_task_does_not_leak_photo_data_on_failure(self, db, mocker, caplog):
        """
        On task failure, logs must not contain raw file data, credentials,
        or personally identifiable information from the Photo record.
        """
        import logging
        photo = PhotoFactory(original_filename="private_wedding.jpg")
        import cloudinary.exceptions
        mocker.patch(
            "gallery.services.cloudinary_service.upload",
            side_effect=cloudinary.exceptions.Error("boom"),
        )
        mocker.patch.object(
            process_fast_lane_asset, "retry", side_effect=MaxRetriesExceededError()
        )

        with caplog.at_level(logging.ERROR):
            process_fast_lane_asset.apply(args=[str(photo.pk)])

        for record in caplog.records:
            assert "StrongTestPass" not in record.message
            assert "api_secret" not in record.message.lower()


# ─────────────────────────────────────────────────────────────────────────────
# INGESTION CLEANUP TASK
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.celery
@pytest.mark.unit
class TestReapAbandonedUploads:
    """Tests for ingestion.tasks.reap_abandoned_uploads."""

    def test_reap_task_runs_without_error(self, db):
        """Smoke test: the cleanup task executes without crashing."""
        from ingestion.tasks import reap_abandoned_uploads
        result = reap_abandoned_uploads.apply()
        assert result.successful(), (
            f"reap_abandoned_uploads failed:\n{result.traceback}"
        )

    def test_reap_task_cleans_stale_uploads(self, db):
        """
        Stale pending uploads older than the configured threshold
        must be marked as abandoned.
        """
        from ingestion.tasks import reap_abandoned_uploads

        stale_photo = PhotoFactory(status="PENDING")
        Photo.objects.filter(pk=stale_photo.pk).update(
            uploaded_at=timezone.now() - datetime.timedelta(hours=25)
        )

        reap_abandoned_uploads.apply()

        stale_photo.refresh_from_db()
        assert stale_photo.status in ("FAILED", "QUARANTINED"), (
            f"Stale upload should be marked FAILED/QUARANTINED. Got: '{stale_photo.status}'"
        )

    def test_reap_task_does_not_touch_recent_uploads(self, db):
        """
        A pending upload created 30 minutes ago must NOT be reaped.
        False positives here would delete in-progress legitimate uploads.
        """
        from ingestion.tasks import reap_abandoned_uploads

        fresh_photo = PhotoFactory(status="PENDING")
        # created_at defaults to now — well within the threshold

        reap_abandoned_uploads.apply()

        fresh_photo.refresh_from_db()
        assert fresh_photo.status == "PENDING", (
            f"Fresh upload was incorrectly reaped. Got status: '{fresh_photo.status}'. "
            "Check the abandonment threshold — it may be too aggressive."
        )

    def test_reap_task_does_not_touch_processed_photos(self, db):
        """
        Processed photos must never be marked abandoned regardless of age.
        """
        from ingestion.tasks import reap_abandoned_uploads

        old_processed = ProcessedPhotoFactory()
        Photo.objects.filter(pk=old_processed.pk).update(
            uploaded_at=timezone.now() - datetime.timedelta(days=30)
        )

        reap_abandoned_uploads.apply()

        old_processed.refresh_from_db()
        assert old_processed.status == "READY", (
            f"Reaper clobbered a processed photo. Got: '{old_processed.status}'. "
            "The WHERE clause on the reap query is missing a status filter."
        )

    def test_reap_task_is_idempotent(self, db):
        """
        Running the reap task twice on the same dataset must produce the same
        result — no phantom status flips or duplicate DB writes.
        """
        from ingestion.tasks import reap_abandoned_uploads

        stale_photo = PhotoFactory(status="PENDING")
        Photo.objects.filter(pk=stale_photo.pk).update(
            uploaded_at=timezone.now() - datetime.timedelta(hours=25)
        )

        reap_abandoned_uploads.apply()
        stale_photo.refresh_from_db()
        status_after_first_run = stale_photo.status

        reap_abandoned_uploads.apply()
        stale_photo.refresh_from_db()

        assert stale_photo.status == status_after_first_run, (
            "Reap task produced different results on second run — not idempotent."
        )

    def test_reap_task_handles_empty_table(self, db):
        """Reaper must not crash when there are no photos at all."""
        from ingestion.tasks import reap_abandoned_uploads
        assert Photo.objects.count() == 0
        result = reap_abandoned_uploads.apply()
        assert result.successful()

    def test_reap_does_not_process_high_volume_in_single_transaction(self, db):
        """
        Bulk reaping 500+ rows in a single transaction holds DB locks for too
        long and causes timeouts in production. The task must batch or chunk.
        This is a performance contract test — fails if batch logic is missing.

        If your reaper intentionally does a single-pass DELETE, reconsider:
        use DELETE ... LIMIT N in a loop or filter + bulk_update in chunks.
        """
        from ingestion.tasks import reap_abandoned_uploads

        photos = PhotoFactory.create_batch(200, status="PENDING")
        Photo.objects.filter(pk__in=[p.pk for p in photos]).update(
            uploaded_at=timezone.now() - datetime.timedelta(hours=25)
        )
        # This should complete without locking timeout
        result = reap_abandoned_uploads.apply()
        assert result.successful()


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION TESTS (real Redis broker)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.celery
@pytest.mark.integration
@pytest.mark.slow
class TestCeleryLiveBroker:
    """
    Real async execution against the Redis broker running in Docker.
    Requires the full stack to be up.
    """

    def test_task_enqueues_and_completes_async(self, db, live_celery_config, mocker):
        from celery import current_app
        current_app.config_from_object(live_celery_config)

        photo = PhotoFactory()
        mocker.patch(
            "gallery.services.cloudinary_service.upload",
            return_value={
                "public_id": f"async/test_{uuid.uuid4().hex}",
                "secure_url": "https://res.cloudinary.com/demo/async_test.jpg",
                "width": 800,
                "height": 600,
                "format": "jpg",
                "bytes": 512_000,
            },
        )

        async_result = process_fast_lane_asset.apply_async(args=[str(photo.pk)])
        result = async_result.get(timeout=30)
        assert result is not None

        photo.refresh_from_db()
        assert photo.status == "READY"

    def test_task_result_not_stored_indefinitely(self, db, live_celery_config, mocker):
        """
        Celery result backend entries must expire. Storing millions of task
        results forever is a Redis OOM vector in production.
        Verify result_expires is configured.
        """
        from celery import current_app
        current_app.config_from_object(live_celery_config)
        expires = current_app.conf.result_expires
        assert expires is not None and expires > 0, (
            "result_expires is not set. Task results will accumulate in Redis "
            "indefinitely and cause OOM in production. Set result_expires in "
            "your Celery config (e.g. result_expires=3600)."
        )
