from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    issue_gallery_access_token,
)
from gallery.models import (
    Event,
    GalleryAccessRole,
    GalleryAccessSession,
    GalleryArchiveJob,
    Photo,
    Scene,
)


User = get_user_model()


@override_settings(
    CLOUDFLARE_R2_ENDPOINT="https://test.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_BUCKET_NAME="test-bucket",
    CLOUDFLARE_ACCESS_KEY_ID="test-key",
    CLOUDFLARE_SECRET_ACCESS_KEY="test-secret",
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
)
class DownloadWorkflowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="downloads@example.com",
            password="StrongPassword123!",
            name="Download Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.owner,
            business_name="Download Studio",
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Client Gallery",
            slug="client-gallery-a1b2c3",
            is_published=True,
        )
        self.scene = Scene.objects.create(event=self.event, title="Ceremony")
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="ready.jpg",
            file_size_bytes=2048,
            r2_object_key="raw/tenant_1/scene_1/ready.jpg",
            status="READY",
            is_processed=True,
        )

        self.owner_client = APIClient()
        self.owner_client.force_authenticate(user=self.owner)
        self.client_gallery_client = APIClient()
        self.anonymous_client = APIClient()

    def _grant_client_gallery_session(self):
        session = GalleryAccessSession.objects.create(
            gallery=self.event,
            email="client@example.com",
            role=GalleryAccessRole.CLIENT,
        )
        self.client_gallery_client.cookies["gallery_access"] = issue_gallery_access_token(
            gallery_id=self.event.id,
            email=session.email,
            role=session.role,
        )
        self.client_gallery_client.cookies["gallery_session"] = (
            encode_gallery_access_session_cookie(session.id)
        )
        return session

    @patch("gallery.storage.get_r2_client")
    def test_owner_can_generate_single_asset_download_url(self, mock_get_r2_client):
        mock_get_r2_client.return_value.generate_presigned_url.return_value = (
            "https://signed.example.com/ready.jpg"
        )

        response = self.owner_client.get(
            reverse("gallery:fastlane-photo-download-url", kwargs={"pk": self.photo.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["requester_kind"], "photographer")
        self.assertEqual(response.data["expires_in_seconds"], 60)
        self.assertIn("download_url", response.data)

    @patch("gallery.storage.get_r2_client")
    def test_verified_client_session_can_generate_single_asset_download_url(self, mock_get_r2_client):
        self._grant_client_gallery_session()
        mock_get_r2_client.return_value.generate_presigned_url.return_value = (
            "https://signed.example.com/client.jpg"
        )

        response = self.client_gallery_client.get(
            reverse("gallery:fastlane-photo-download-url", kwargs={"pk": self.photo.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["requester_kind"], "client")
        self.assertEqual(response.data["delivery_mode"], "direct_r2_presigned_get")

    def test_unready_asset_cannot_generate_download_url(self):
        self._grant_client_gallery_session()
        self.photo.status = "PENDING"
        self.photo.is_processed = False
        self.photo.save(update_fields=["status", "is_processed"])

        response = self.client_gallery_client.get(
            reverse("gallery:fastlane-photo-download-url", kwargs={"pk": self.photo.id})
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("gallery.client_views.build_gallery_archive.delay")
    def test_bulk_download_requires_verified_access_and_returns_archive_job(self, mock_delay):
        denied = self.anonymous_client.post(
            reverse("gallery_public:archive-request", args=[self.event.id])
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        mock_delay.assert_not_called()

        self._grant_client_gallery_session()
        allowed = self.client_gallery_client.post(
            reverse("gallery_public:archive-request", args=[self.event.id])
        )

        self.assertEqual(allowed.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(allowed.data["status"], GalleryArchiveJob.Status.PENDING)
        self.assertIn("archive_job_id", allowed.data)
        archive_job = GalleryArchiveJob.objects.get(id=allowed.data["archive_job_id"])
        mock_delay.assert_called_once_with(str(archive_job.id))
