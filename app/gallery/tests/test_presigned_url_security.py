from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import Workspace
from gallery.models import Event, Photo, Scene
from gallery.storage import DOWNLOAD_URL_TTL_SECONDS


User = get_user_model()


@override_settings(
    CLOUDFLARE_R2_ENDPOINT="https://test.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_BUCKET_NAME="test-bucket",
    CLOUDFLARE_ACCESS_KEY_ID="test-key",
    CLOUDFLARE_SECRET_ACCESS_KEY="test-secret",
)
class PresignedDownloadSecurityTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(
            email="download@example.com",
            password="StrongPassword123!",
            name="Download User",
            accepted_terms=True,
        )
        workspace = Workspace.objects.create(user=user, business_name="Download Studio")
        event = Event.objects.create(workspace=workspace, title="Wedding", slug="wedding")
        scene = Scene.objects.create(event=event, title="Ceremony")
        self.photo = Photo.objects.create(
            scene=scene,
            original_filename="highres.jpg",
            file_size_bytes=5_000_000,
            status="READY",
            is_processed=True,
            r2_object_key="fast-lane/tenant_1/photo_1/highres.jpg",
        )

    @patch("gallery.storage.get_r2_client")
    def test_download_url_uses_r2_presigned_get(self, mock_get_r2_client):
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = (
            "https://test-bucket.r2.cloudflarestorage.com/highres.jpg"
            "?X-Amz-Signature=abc123"
        )
        mock_get_r2_client.return_value = mock_client

        url = self.photo.download_url

        self.assertIsNotNone(url)
        self.assertIn("cloudflarestorage.com", url)
        self.assertNotIn("res.cloudinary.com", url)
        mock_client.generate_presigned_url.assert_called_once()

    @patch("gallery.storage.get_r2_client")
    def test_download_url_ttl_is_capped_at_security_ceiling(self, mock_get_r2_client):
        mock_client = MagicMock()
        mock_client.generate_presigned_url.return_value = "https://signed.example.com"
        mock_get_r2_client.return_value = mock_client

        _ = self.photo.download_url

        self.assertEqual(
            mock_client.generate_presigned_url.call_args.kwargs["ExpiresIn"],
            DOWNLOAD_URL_TTL_SECONDS,
        )

