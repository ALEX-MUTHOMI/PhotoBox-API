from unittest.mock import MagicMock, patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import Workspace
from gallery import storage as gallery_storage
from gallery.models import (
    Event,
    FavoriteSelection,
    GalleryAccessSession,
    GalleryArchiveJob,
    Photo,
    Scene,
    VisibilityChoices,
)
from gallery.storage import delete_r2_objects, get_r2_delete_client
from user.serializers import UserSerializer


User = get_user_model()


@override_settings(
    CURRENT_TOS_VERSION="2026-04",
    CLOUDFLARE_R2_ENDPOINT="https://test.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_BUCKET_NAME="test-bucket",
    CLOUDFLARE_ACCESS_KEY_ID="test-key",
    CLOUDFLARE_SECRET_ACCESS_KEY="test-secret",
    CLOUDFLARE_R2_DELETE_ENDPOINT="https://delete.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_DELETE_BUCKET_NAME="delete-bucket",
    CLOUDFLARE_R2_DELETE_ACCESS_KEY_ID="delete-key",
    CLOUDFLARE_R2_DELETE_SECRET_ACCESS_KEY="delete-secret",
)
class GDPRComplianceTests(TestCase):
    def test_user_serializer_persists_tos_metadata_when_terms_are_accepted(self):
        serializer = UserSerializer(data={
            "email": "new@example.com",
            "password": "StrongPassword123!",
            "name": "New User",
            "accepted_terms": True,
        })

        self.assertTrue(serializer.is_valid(), serializer.errors)
        user = serializer.save()

        self.assertTrue(user.accepted_terms)
        self.assertIsNotNone(user.tos_accepted_at)
        self.assertEqual(user.tos_version, "2026-04")

    @patch("core.signals.purge_deleted_photographer_assets.delay")
    def test_deleting_photographer_queues_asset_purge_with_photo_and_archive_keys(self, mock_delay):
        user = User.objects.create_user(
            email="delete-me@example.com",
            password="StrongPassword123!",
            name="Delete Me",
            accepted_terms=True,
        )
        workspace = Workspace.objects.create(user=user, business_name="Delete Studio")
        gallery = Event.objects.create(
            workspace=workspace,
            title="GDPR Gallery",
            slug="gdpr-gallery",
            is_published=True,
        )
        scene = Scene.objects.create(event=gallery, title="Scene", visibility=VisibilityChoices.PUBLIC)
        Photo.objects.create(
            scene=scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="photo.jpg",
            file_size_bytes=100,
            status="READY",
            is_processed=True,
            r2_object_key="tenant/photo.jpg",
            web_r2_object_key="web/photo.webp",
        )
        GalleryArchiveJob.objects.create(
            gallery=gallery,
            status=GalleryArchiveJob.Status.COMPLETED,
            r2_zip_key="archives/gallery.zip",
        )

        with self.captureOnCommitCallbacks(execute=True):
            user.delete()

        mock_delay.assert_called_once()
        args = mock_delay.call_args.args
        self.assertEqual(args[0], str(user.id))
        self.assertCountEqual(args[1], ["tenant/photo.jpg", "web/photo.webp", "archives/gallery.zip"])

    @patch("gallery.storage.get_r2_delete_client")
    def test_delete_r2_objects_uses_batch_delete(self, mock_get_delete_client):
        mock_client = MagicMock()
        mock_get_delete_client.return_value = mock_client
        keys = [f"tenant/object-{index}.jpg" for index in range(1001)]

        deleted = delete_r2_objects(keys)

        self.assertTrue(deleted)
        self.assertEqual(mock_client.delete_objects.call_count, 2)

    @patch("gallery.storage._build_r2_client")
    def test_get_r2_delete_client_uses_delete_credentials_when_configured(self, mock_build_client):
        mock_build_client.return_value = MagicMock()
        gallery_storage._thread_local.delete_client_fingerprint = None

        get_r2_delete_client()

        args = mock_build_client.call_args.args
        self.assertEqual(args[0], "https://delete.r2.cloudflarestorage.com")
        self.assertEqual(args[1], "delete-key")
        self.assertEqual(args[2], "delete-secret")

    def test_admin_registers_archive_and_access_models(self):
        self.assertIn(User, admin.site._registry)
        self.assertIn(Workspace, admin.site._registry)
        self.assertIn(Photo, admin.site._registry)
        self.assertIn(Scene, admin.site._registry)
        self.assertIn(GalleryArchiveJob, admin.site._registry)
        self.assertIn(GalleryAccessSession, admin.site._registry)
        self.assertIn(FavoriteSelection, admin.site._registry)
