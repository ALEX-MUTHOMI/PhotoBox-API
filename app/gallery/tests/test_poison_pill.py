"""Poison-pill images mark FAILED, refund quota once, enqueue purge, no retry."""
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from PIL import Image as PILImage

from core.models import Workspace
from gallery.models import Event, Photo, Scene
from gallery.tasks import generate_photo_web_derivative

User = get_user_model()


@override_settings(
    CLOUDFLARE_R2_DOMAIN="cdn.example.test",
    CLOUDFLARE_R2_BUCKET_NAME="bucket",
    CELERY_TASK_ALWAYS_EAGER=True,
)
class PoisonPillDerivativeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="poison@example.com",
            password="StrongPassword123!",
            name="Poison",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Studio",
            storage_used_bytes=2_000_000,
            storage_limit_bytes=50_000_000_000,
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Wedding",
            slug="poison-wed",
            is_published=True,
        )
        self.scene = Scene.objects.create(event=self.gallery, title="Ceremony")
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="bomb.jpg",
            file_size_bytes=1_000_000,
            status="READY",
            r2_object_key="tenant/orig/bomb.jpg",
            is_processed=True,
            media_type="IMAGE",
        )

    def test_decompression_bomb_marks_failed_refunds_once_enqueues_purge(self):
        body = MagicMock()
        body.read.side_effect = [b"not-an-image", b""]
        body.__enter__ = MagicMock(return_value=body)
        body.__exit__ = MagicMock(return_value=False)

        with patch("gallery.storage.get_r2_client") as mock_r2:
            mock_r2.return_value.get_object.return_value = {"Body": body}
            with patch(
                "gallery.tasks.PILImage.open",
                side_effect=PILImage.DecompressionBombError("too many pixels"),
            ):
                with patch(
                    "gallery.asset_purge.purge_photo_assets_task.delay"
                ) as mock_delay:
                    with self.captureOnCommitCallbacks(execute=True):
                        result = generate_photo_web_derivative(str(self.photo.id))

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "unrecoverable_image")
        self.photo.refresh_from_db()
        self.assertEqual(self.photo.status, "FAILED")
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 1_000_000)
        mock_delay.assert_called_once()

        with patch(
            "gallery.asset_purge.purge_photo_assets_task.delay"
        ) as mock_delay2:
            with self.captureOnCommitCallbacks(execute=True):
                from gallery.photo_failure import mark_photo_failed_and_release_quota

                again = mark_photo_failed_and_release_quota(self.photo.id)
        self.assertFalse(again)
        mock_delay2.assert_not_called()
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 1_000_000)
