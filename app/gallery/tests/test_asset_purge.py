"""Asset purge: Cloudinary type=fetch + on_commit enqueue; no HTTP-path I/O."""
import sys
import types
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Workspace
from gallery.asset_purge import invalidate_cloudinary_fetch, purge_photo_asset_keys
from gallery.models import Event, Photo, Scene

User = get_user_model()


@override_settings(
    CLOUDFLARE_R2_DOMAIN="cdn.example.test",
    CLOUDINARY_CLOUD_NAME="photobox-test",
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
)
class AssetPurgeUnitTests(TestCase):
    def test_invalidate_cloudinary_fetch_uses_type_fetch(self):
        destroy = MagicMock()
        uploader = types.SimpleNamespace(destroy=destroy)
        cloudinary_mod = types.SimpleNamespace(uploader=uploader)
        with patch.dict(
            sys.modules,
            {"cloudinary": cloudinary_mod, "cloudinary.uploader": uploader},
        ):
            ok = invalidate_cloudinary_fetch("https://cdn.example.test/web/a.webp")
        self.assertTrue(ok)
        destroy.assert_called_once()
        args, kwargs = destroy.call_args
        self.assertEqual(args[0], "https://cdn.example.test/web/a.webp")
        self.assertEqual(kwargs.get("type"), "fetch")
        self.assertTrue(kwargs.get("invalidate"))

    @patch("gallery.asset_purge.delete_r2_objects", return_value=True)
    @patch("gallery.asset_purge.invalidate_cloudinary_fetch", return_value=True)
    def test_purge_deletes_r2_and_invalidates_cdn(self, mock_invalidate, mock_delete):
        result = purge_photo_asset_keys(
            r2_object_key="orig/a.jpg",
            web_r2_object_key="web/a.webp",
            r2_public_url="https://cdn.example.test/web/a.webp",
        )
        mock_delete.assert_called_once()
        mock_invalidate.assert_called_once_with("https://cdn.example.test/web/a.webp")
        self.assertEqual(result["status"], "purged")


@override_settings(
    CLOUDFLARE_R2_DOMAIN="cdn.example.test",
    CLOUDINARY_CLOUD_NAME="photobox-test",
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    CELERY_TASK_ALWAYS_EAGER=True,
)
class FastLaneDestroyPurgeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="purge@example.com",
            password="StrongPassword123!",
            name="Purge",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Studio",
            storage_used_bytes=5_000_000,
            storage_limit_bytes=50_000_000_000,
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Wedding",
            slug="purge-wed",
            is_published=True,
        )
        self.scene = Scene.objects.create(event=self.gallery, title="Ceremony")
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="hero.jpg",
            file_size_bytes=1_000_000,
            status="READY",
            r2_object_key="tenant/orig/hero.jpg",
            web_r2_object_key="tenant/web/hero.webp",
            is_processed=True,
        )
        self.client = APIClient()
        token = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")

    def test_destroy_enqueues_purge_on_commit_without_inline_network(self):
        url = reverse("gallery:fastlane-photo-detail", args=[self.photo.id])
        with patch("gallery.asset_purge.purge_photo_assets_task.delay") as mock_delay:
            with patch("cloudinary.uploader.destroy") as mock_destroy:
                with patch("gallery.storage.delete_r2_objects") as mock_r2:
                    with self.captureOnCommitCallbacks(execute=True):
                        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        mock_destroy.assert_not_called()
        mock_r2.assert_not_called()
        mock_delay.assert_called_once()
        args = mock_delay.call_args[0]
        self.assertEqual(args[0], "tenant/orig/hero.jpg")
        self.assertEqual(args[1], "tenant/web/hero.webp")
        self.assertEqual(args[2], "https://cdn.example.test/tenant/web/hero.webp")
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 4_000_000)

    def test_destroy_does_not_enqueue_purge_when_transaction_rolls_back(self):
        url = reverse("gallery:fastlane-photo-detail", args=[self.photo.id])
        with patch("gallery.asset_purge.purge_photo_assets_task.delay") as mock_delay:
            with patch(
                "gallery.views.release_workspace_bytes",
                side_effect=RuntimeError("boom"),
            ):
                with self.assertRaises(RuntimeError):
                    with self.captureOnCommitCallbacks(execute=True):
                        self.client.delete(url)
        mock_delay.assert_not_called()
        self.assertTrue(Photo.objects.filter(id=self.photo.id).exists())
