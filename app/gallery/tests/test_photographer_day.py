"""Phase C: client_phone, upload-plan, publish requires PIN."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Workspace
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    issue_gallery_access_token,
)
from gallery.models import (
    ClientAllowlist,
    Event,
    GalleryAccessRole,
    GalleryAccessSession,
    Photo,
    Scene,
    VisibilityChoices,
)


User = get_user_model()


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    FRONTEND_URL="https://app.photobox.test",
)
class PhotographerDayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(user=self.user, business_name="Studio")
        self.client = APIClient()
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(self.user).access_token}"
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Nairobi Wedding",
            slug="nai-1",
            is_published=False,
        )

    def test_invalid_phone_rejected(self):
        url = reverse("gallery:event-detail", args=[self.event.id])
        res = self.client.patch(url, {"client_phone": "07"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valid_phone_saved_but_absent_from_public_payload(self):
        url = reverse("gallery:event-detail", args=[self.event.id])
        res = self.client.patch(
            url,
            {"client_phone": "+254712345678", "gallery_pin": "secret9", "is_published": True},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["client_phone"], "+254712345678")
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_published)

        scene = Scene.objects.create(
            event=self.event,
            title="Ceremony",
            visibility=VisibilityChoices.PUBLIC,
        )
        Photo.objects.create(
            scene=scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="a.jpg",
            file_size_bytes=10,
            status="READY",
            is_processed=True,
            web_r2_object_key="gallery/web/a.webp",
        )
        guest = APIClient()
        session = GalleryAccessSession.objects.create(
            gallery=self.event,
            email="g@example.com",
            role=GalleryAccessRole.GUEST,
        )
        token = issue_gallery_access_token(
            self.event.id,
            "g@example.com",
            GalleryAccessRole.GUEST,
            pin_version=self.event.pin_version,
        )
        guest.cookies["gallery_access"] = token
        guest.cookies["gallery_session"] = encode_gallery_access_session_cookie(session.id)
        pub = guest.get(reverse("gallery_public:detail", args=[self.event.share_code]))
        self.assertEqual(pub.status_code, status.HTTP_200_OK)
        self.assertNotIn("client_phone", pub.data["gallery"])

    def test_publish_without_pin_rejected(self):
        url = reverse("gallery:event-detail", args=[self.event.id])
        res = self.client.patch(url, {"is_published": True}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_published)

    def test_upload_plan_lanes(self):
        url = reverse("gallery:upload-plan")
        self.assertEqual(
            self.client.post(url, {"filename": "a.jpg", "size_bytes": 4_000_000}, format="json").data["lane"],
            "fast",
        )
        self.assertEqual(
            self.client.post(url, {"filename": "a.jpg", "size_bytes": 20_000_000}, format="json").data["lane"],
            "heavy",
        )
        self.assertEqual(
            self.client.post(url, {"filename": "clip.mp4", "size_bytes": 1000}, format="json").data["lane"],
            "heavy",
        )

    def test_allowlist_phone_does_not_change_magic_link_enumeration(self):
        self.event.set_pin("secret9")
        self.event.is_published = True
        self.event.save(update_fields=["is_published"])
        ClientAllowlist.objects.create(
            gallery=self.event,
            email="bride@example.com",
            phone="+254700000001",
        )
        unknown = APIClient().post(
            reverse("gallery_public:magic-link-request", args=[self.event.share_code]),
            {"email": "stranger@example.com"},
            format="json",
        )
        known = APIClient().post(
            reverse("gallery_public:magic-link-request", args=[self.event.share_code]),
            {"email": "bride@example.com"},
            format="json",
        )
        self.assertEqual(unknown.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(known.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(unknown.data, known.data)

    @patch("gallery.tasks.process_fast_lane_asset")
    def test_fast_lane_still_rejects_6mb_even_if_plan_said_fast(self, _mock_task):
        scene = Scene.objects.create(event=self.event, title="Highlight")
        huge = SimpleUploadedFile(
            "massive.jpg",
            b"0" * (6 * 1024 * 1024),
            content_type="image/jpeg",
        )
        res = self.client.post(
            reverse("gallery:fastlane-photo-list"),
            {"scene": scene.id, "image_file": huge},
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        _mock_task.delay.assert_not_called()
