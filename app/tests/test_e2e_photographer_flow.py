from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Photo, Scene
from gallery.notifications import send_gallery_ready_email
from gallery.tasks import process_fast_lane_asset


User = get_user_model()


@override_settings(
    CLOUDFLARE_R2_ENDPOINT="https://test.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_BUCKET_NAME="test-bucket",
    CLOUDFLARE_ACCESS_KEY_ID="test-key",
    CLOUDFLARE_SECRET_ACCESS_KEY="test-secret",
    CLOUDFLARE_R2_DOMAIN="cdn.photobox-vault.com",
    CLOUDINARY_CLOUD_NAME="photobox-prod",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    FRONTEND_URL="https://app.photobox.test",
)
class PhotographerFlowE2ETests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.token_url = reverse("user:token")
        self.upload_url = reverse("gallery:fastlane-photo-list")

        self.user = User.objects.create_user(
            email="photographer@example.com",
            password="StrongPassword123!",
            name="Photographer",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="PhotoBox Studio",
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Wedding Day",
            slug="wedding-day",
            client_email="client@example.com",
            client_name="Client Name",
        )
        self.scene = Scene.objects.create(event=self.event, title="Ceremony")

    def _authenticate(self):
        response = self.client.post(
            self.token_url,
            {"email": self.user.email, "password": "StrongPassword123!"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

    @patch("gallery.tasks.process_fast_lane_asset.delay")
    def test_photographer_upload_flow_reaches_ready_state_and_delivery_urls(self, mock_delay):
        self._authenticate()

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image as PILImage
        import io

        buffer = io.BytesIO()
        PILImage.new("RGB", (64, 64), color=(8, 16, 32)).save(buffer, format="JPEG")
        buffer.seek(0)
        file = SimpleUploadedFile("hero.jpg", buffer.read(), content_type="image/jpeg")

        response = self.client.post(
            self.upload_url,
            {"scene": str(self.scene.id), "image_file": file},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        photo = Photo.objects.get(id=response.data["photo_id"])
        mock_delay.assert_called_once_with(str(photo.id))

        with patch("gallery.storage.get_r2_client") as mock_get_r2_client, patch(
            "gallery.tasks.generate_photo_web_derivative.apply_async"
        ), patch(
            "gallery.tasks.compute_photo_phash.apply_async"
        ):
            mock_client = MagicMock()
            mock_client.head_object.return_value = {"ContentLength": photo.file_size_bytes}
            mock_client.generate_presigned_url.return_value = "https://signed.example.com/download"
            mock_get_r2_client.return_value = mock_client

            result = process_fast_lane_asset(photo_id=str(photo.id))

            self.assertEqual(result["status"], "self_healed")
            photo.refresh_from_db()
            self.assertEqual(photo.status, "READY")
            self.assertTrue(photo.is_processed)
            self.assertIn("res.cloudinary.com/photobox-prod/image/fetch", photo.delivery_url)
            self.assertIn("q_auto,f_webp", photo.delivery_url)
            self.assertEqual(photo.download_url, "https://signed.example.com/download")

    @patch("gallery.notifications.send_gallery_ready_email.delay")
    def test_publishing_gallery_queues_client_email_notification(self, mock_delay):
        self._authenticate()

        response = self.client.patch(
            reverse("gallery:event-detail", args=[self.event.id]),
            {"is_published": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_delay.assert_called_once_with(str(self.event.id))

    def test_gallery_ready_email_sends_expected_mobile_friendly_content(self):
        self.event.is_published = True
        self.event.save(update_fields=["is_published"])

        send_gallery_ready_email.apply(args=[str(self.event.id)])

        self.assertEqual(len(mail.outbox), 1)
        delivered = mail.outbox[0]
        self.assertEqual(delivered.to, ["client@example.com"])
        self.assertIn("Wedding Day", delivered.subject)
        self.assertIn("https://app.photobox.test/gallery/wedding-day", delivered.body)
