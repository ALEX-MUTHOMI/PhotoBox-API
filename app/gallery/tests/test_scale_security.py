"""Scale-security unit tests: ZIP lease, pk batches, throttle keys, archive queue."""
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIRequestFactory

from gallery.db_batch import iter_pk_batches
from gallery.models import Event, Photo, Scene
from gallery.tasks import build_gallery_archive
from gallery.throttles import FavoriteSelectionThrottle, MagicLinkConsumeThrottle
from gallery.zip_lease import acquire_zip_lease, release_zip_lease


class ArchiveQueueBindingTests(SimpleTestCase):
    def test_archive_task_uses_dedicated_queue(self):
        self.assertEqual(getattr(build_gallery_archive, "queue", None), "archive-zip")


class ZipLeaseFailOpenTests(SimpleTestCase):
    @override_settings(RATE_LIMIT_REDIS_URL="")
    def test_acquire_fail_opens_without_redis(self):
        decision = acquire_zip_lease("job-1", "gallery-1")
        self.assertTrue(decision.acquired)
        self.assertIsNotNone(decision.lease)
        release_zip_lease(decision.lease)


class ZipLeaseLuaPathTests(SimpleTestCase):
    @patch("gallery.zip_lease._client")
    def test_release_requires_holder_match(self, mock_client_factory):
        client = MagicMock()
        mock_client_factory.return_value = client
        client.eval.side_effect = [[1, b"ok"], 1]

        decision = acquire_zip_lease("job-2", "gallery-2")
        self.assertTrue(decision.acquired)
        release_zip_lease(decision.lease)
        self.assertEqual(client.eval.call_count, 2)


class MagicLinkConsumeThrottleKeyTests(SimpleTestCase):
    def test_consume_key_includes_token_digest_not_raw_token(self):
        factory = APIRequestFactory()
        request = factory.post("/api/galleries/magic-link/consume/")
        request.data = {"token": "super-secret-raw-token-value"}
        throttle = MagicLinkConsumeThrottle()
        suffix = throttle.get_ident_suffix(request, view=None)
        self.assertNotIn("super-secret-raw-token-value", suffix)
        self.assertIn(":", suffix)
        # Different tokens => different buckets (same IP)
        request2 = factory.post("/api/galleries/magic-link/consume/")
        request2.data = {"token": "another-token"}
        suffix2 = throttle.get_ident_suffix(request2, view=None)
        self.assertNotEqual(suffix, suffix2)


class FavoriteSelectionThrottleKeyTests(SimpleTestCase):
    def test_favorites_key_uses_gallery_and_session(self):
        factory = APIRequestFactory()
        request = factory.post("/x/")
        request.COOKIES = {}
        throttle = FavoriteSelectionThrottle()

        class View:
            kwargs = {"gallery_id": "g1"}

        # Missing session => no throttle key (auth layer rejects)
        self.assertIsNone(throttle.get_cache_key(request, View()))


class PkBatchIterationTests(TestCase):
    def test_iter_pk_batches_covers_all_rows(self):
        from django.contrib.auth import get_user_model

        from core.models import Workspace

        User = get_user_model()
        user = User.objects.create_user(
            email="batch@test.com",
            password="password123",
            name="Batch",
            accepted_terms=True,
        )
        workspace = Workspace.objects.create(user=user, business_name="Batch")
        event = Event.objects.create(workspace=workspace, title="E", slug="batch-e")
        scene = Scene.objects.create(event=event, title="S")
        Photo.objects.bulk_create(
            [
                Photo(
                    scene=scene,
                    original_filename=f"{i}.jpg",
                    file_size_bytes=1,
                    status="READY",
                )
                for i in range(7)
            ]
        )
        ids = [p.id for p in iter_pk_batches(Photo.objects.filter(scene=scene), chunk_size=3)]
        self.assertEqual(len(ids), 7)
        self.assertEqual(len(set(ids)), 7)
