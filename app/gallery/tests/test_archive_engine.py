import io
import zipfile
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    issue_gallery_access_token,
)
from gallery.models import (
    Event,
    FavoriteSelection,
    GalleryAccessRole,
    GalleryAccessSession,
    GalleryArchiveJob,
    GalleryArchiveType,
    Photo,
    Scene,
    VisibilityChoices,
)
from gallery.tasks import build_gallery_archive


User = get_user_model()


class _FakeStreamingBody:
    def __init__(self, payload: bytes):
        self.buffer = io.BytesIO(payload)

    def read(self, size=-1):
        return self.buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.buffer.close()
        return False


@override_settings(
    CLOUDFLARE_R2_ENDPOINT="https://test.r2.cloudflarestorage.com",
    CLOUDFLARE_R2_BUCKET_NAME="test-bucket",
    CLOUDFLARE_ACCESS_KEY_ID="test-key",
    CLOUDFLARE_SECRET_ACCESS_KEY="test-secret",
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
)
class GalleryArchiveEngineTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="archive@example.com",
            password="StrongPassword123!",
            name="Archive User",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Archive Studio",
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Wedding Archive",
            slug="wedding-archive",
            is_published=True,
        )
        self.scene = Scene.objects.create(
            event=self.gallery,
            title="Ceremony",
            visibility=VisibilityChoices.PUBLIC,
        )
        self.public_photo = Photo.objects.create(
            scene=self.scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="public.jpg",
            file_size_bytes=128,
            status="READY",
            is_processed=True,
            r2_object_key="tenant/gallery/public.jpg",
        )
        self.client_only_photo = Photo.objects.create(
            scene=self.scene,
            visibility=VisibilityChoices.CLIENT_ONLY,
            original_filename="client.jpg",
            file_size_bytes=256,
            status="READY",
            is_processed=True,
            r2_object_key="tenant/gallery/client.jpg",
        )
        self.hidden_photo = Photo.objects.create(
            scene=self.scene,
            visibility="HIDDEN",
            original_filename="hidden.jpg",
            file_size_bytes=512,
            status="READY",
            is_processed=True,
            r2_object_key="tenant/gallery/hidden.jpg",
        )
        self.pending_photo = Photo.objects.create(
            scene=self.scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="pending.jpg",
            file_size_bytes=512,
            status="PENDING",
            is_processed=False,
            r2_object_key="tenant/gallery/pending.jpg",
        )

    def _client_token(self, gallery_id=None):
        return issue_gallery_access_token(
            gallery_id=gallery_id or self.gallery.id,
            email="bride@example.com",
            role="CLIENT",
        )

    def _set_gallery_session_cookie(self, role="CLIENT", email="bride@example.com"):
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email=email,
            role=role,
        )
        self.client.cookies["gallery_access"] = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email=email,
            role=role,
        )
        self.client.cookies["gallery_session"] = encode_gallery_access_session_cookie(session.id)
        return session

    @patch("gallery.storage.upload_local_file_to_r2")
    @patch("gallery.storage.get_r2_client")
    def test_archive_task_streams_only_allowed_ready_assets(self, mock_get_r2_client, mock_upload):
        job = GalleryArchiveJob.objects.create(gallery=self.gallery)
        archived_names = []

        mock_client = MagicMock()
        payloads = {
            self.public_photo.r2_object_key: b"public-bytes",
            self.client_only_photo.r2_object_key: b"client-bytes",
            self.hidden_photo.r2_object_key: b"hidden-bytes",
            self.pending_photo.r2_object_key: b"pending-bytes",
        }

        def fake_get_object(Bucket, Key):
            return {"Body": _FakeStreamingBody(payloads[Key])}

        def fake_upload(path, key, content_type="application/octet-stream"):
            with zipfile.ZipFile(path, "r") as archive_file:
                archived_names.extend(archive_file.namelist())
                self.assertIn("ceremony/public-", archived_names[0])
                self.assertEqual(
                    archive_file.read(archived_names[0]),
                    b"public-bytes",
                )
            self.assertEqual(content_type, "application/zip")
            self.assertIn(str(job.id), key)
            return True

        mock_client.get_object.side_effect = fake_get_object
        mock_get_r2_client.return_value = mock_client
        mock_upload.side_effect = fake_upload

        result = build_gallery_archive(archive_job_id=str(job.id))

        self.assertEqual(result["status"], "completed")
        job.refresh_from_db()
        self.assertEqual(job.status, GalleryArchiveJob.Status.COMPLETED)
        self.assertIsNotNone(job.r2_zip_key)
        self.assertEqual(len(archived_names), 2)
        self.assertTrue(any("public-" in name for name in archived_names))
        self.assertTrue(any("client-" in name for name in archived_names))
        self.assertFalse(any("hidden" in name for name in archived_names))
        self.assertFalse(any("pending" in name for name in archived_names))

    @patch("gallery.client_views.generate_r2_presigned_get_url")
    def test_archive_status_returns_short_lived_url_for_matching_gallery_scope(self, mock_presign):
        GalleryArchiveJob.objects.create(
            gallery=self.gallery,
            status=GalleryArchiveJob.Status.COMPLETED,
            r2_zip_key="archives/test.zip",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        mock_presign.return_value = "https://signed.example.com/archive.zip"

        response = self.client.get(
            reverse("gallery_public:archive-status", args=[self.gallery.id]),
            HTTP_AUTHORIZATION=f"Bearer {self._client_token()}",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], GalleryArchiveJob.Status.COMPLETED)
        self.assertEqual(response.data["download_url"], "https://signed.example.com/archive.zip")
        self.assertEqual(
            mock_presign.call_args.kwargs["expires_in"],
            60,
        )

    def test_archive_status_rejects_gallery_scope_mismatch(self):
        other_gallery = Event.objects.create(
            workspace=self.workspace,
            title="Other Gallery",
            slug="other-gallery",
            is_published=True,
        )
        GalleryArchiveJob.objects.create(gallery=self.gallery, status=GalleryArchiveJob.Status.PENDING)

        response = self.client.get(
            reverse("gallery_public:archive-status", args=[self.gallery.id]),
            HTTP_AUTHORIZATION=f"Bearer {self._client_token(gallery_id=other_gallery.id)}",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("gallery.client_views.build_gallery_archive.delay")
    def test_archive_request_queues_job_for_client(self, mock_delay):
        response = self.client.post(
            reverse("gallery_public:archive-request", args=[self.gallery.id]),
            HTTP_AUTHORIZATION=f"Bearer {self._client_token()}",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(GalleryArchiveJob.objects.filter(gallery=self.gallery).count(), 1)
        job = GalleryArchiveJob.objects.get(gallery=self.gallery)
        mock_delay.assert_called_once_with(str(job.id))

    @patch("gallery.storage.upload_local_file_to_r2")
    @patch("gallery.storage.get_r2_client")
    def test_favorites_archive_task_streams_only_current_session_selections(
        self,
        mock_get_r2_client,
        mock_upload,
    ):
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="guest@example.com",
            role=GalleryAccessRole.GUEST,
        )
        FavoriteSelection.objects.create(session=session, photo=self.public_photo)
        FavoriteSelection.objects.create(session=session, photo=self.client_only_photo)
        job = GalleryArchiveJob.objects.create(
            gallery=self.gallery,
            access_session=session,
            archive_type=GalleryArchiveType.FAVORITES,
        )
        archived_names = []

        mock_client = MagicMock()
        payloads = {
            self.public_photo.r2_object_key: b"public-bytes",
            self.client_only_photo.r2_object_key: b"client-bytes",
        }

        def fake_get_object(Bucket, Key):
            return {"Body": _FakeStreamingBody(payloads[Key])}

        def fake_upload(path, key, content_type="application/octet-stream"):
            with zipfile.ZipFile(path, "r") as archive_file:
                archived_names.extend(archive_file.namelist())
            self.assertIn("favorites/session_", key)
            self.assertEqual(content_type, "application/zip")
            return True

        mock_client.get_object.side_effect = fake_get_object
        mock_get_r2_client.return_value = mock_client
        mock_upload.side_effect = fake_upload

        result = build_gallery_archive(archive_job_id=str(job.id))

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(archived_names), 1)
        self.assertTrue(any("public-" in name for name in archived_names))
        self.assertFalse(any("client-" in name for name in archived_names))

    @patch("gallery.client_views.build_gallery_archive.delay")
    def test_favorites_archive_request_queues_job_for_scoped_session(self, mock_delay):
        session = self._set_gallery_session_cookie(
            role=GalleryAccessRole.CLIENT,
            email="bride@example.com",
        )
        FavoriteSelection.objects.create(session=session, photo=self.public_photo)

        response = self.client.post(
            reverse("gallery_public:favorites-archive-request", args=[self.gallery.id]),
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        job = GalleryArchiveJob.objects.get(
            gallery=self.gallery,
            archive_type=GalleryArchiveType.FAVORITES,
            access_session=session,
        )
        mock_delay.assert_called_once_with(str(job.id))

    @patch("gallery.client_views.generate_r2_presigned_get_url")
    def test_favorites_archive_status_uses_matching_session_job(self, mock_presign):
        session = self._set_gallery_session_cookie(
            role=GalleryAccessRole.GUEST,
            email="guest@example.com",
        )
        other_session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="other@example.com",
            role=GalleryAccessRole.GUEST,
        )
        GalleryArchiveJob.objects.create(
            gallery=self.gallery,
            access_session=other_session,
            archive_type=GalleryArchiveType.FAVORITES,
            status=GalleryArchiveJob.Status.COMPLETED,
            r2_zip_key="archives/other.zip",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        GalleryArchiveJob.objects.create(
            gallery=self.gallery,
            access_session=session,
            archive_type=GalleryArchiveType.FAVORITES,
            status=GalleryArchiveJob.Status.COMPLETED,
            r2_zip_key="archives/mine.zip",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        mock_presign.return_value = "https://signed.example.com/favorites.zip"

        response = self.client.get(
            reverse("gallery_public:favorites-archive-status", args=[self.gallery.id]),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["download_url"], "https://signed.example.com/favorites.zip")
        self.assertEqual(mock_presign.call_args.kwargs["key"], "archives/mine.zip")
