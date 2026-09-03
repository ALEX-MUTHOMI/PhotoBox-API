"""A8: photographer workspace branding and custom-domain API."""
import io

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from core.domain_index import get_workspace_id_by_domain
from core.models import Workspace

User = get_user_model()
WORKSPACE_URL = reverse("gallery:workspace-branding")


def _png_upload(name="logo.png", size=(32, 32)):
    buffer = io.BytesIO()
    PILImage.new("RGBA", size, color=(10, 20, 30, 255)).save(buffer, format="PNG")
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="image/png")


def _jpeg_upload(name="not-png.jpg"):
    buffer = io.BytesIO()
    PILImage.new("RGB", (16, 16), color=(1, 2, 3)).save(buffer, format="JPEG")
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="image/jpeg")


class WorkspaceBrandingApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="brand@example.com",
            password="StrongPassword123!",
            name="Brand",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Brand Studio",
            storage_used_bytes=12345,
        )
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}"
        )

    def tearDown(self):
        cache.clear()

    def test_unauthenticated_returns_401(self):
        self.client.credentials()
        res = self.client.get(WORKSPACE_URL)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_client_jwt_returns_403(self):
        self.user.gallery_id = "00000000-0000-0000-0000-000000000001"
        self.client.force_authenticate(user=self.user)
        res = self.client.get(WORKSPACE_URL)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_get_and_patch_own_workspace(self):
        res = self.client.get(WORKSPACE_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["business_name"], "Brand Studio")
        self.assertNotIn("storage_used_bytes", res.data)

        patched = self.client.patch(
            WORKSPACE_URL,
            {"business_name": "Renamed Studio", "brand_color": "#112233"},
            format="json",
        )
        self.assertEqual(patched.status_code, status.HTTP_200_OK)
        self.assertEqual(patched.data["business_name"], "Renamed Studio")
        self.assertEqual(patched.data["brand_color"], "#112233")
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 12345)

    def test_put_not_allowed(self):
        res = self.client.put(
            WORKSPACE_URL,
            {"business_name": "Nope"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_invalid_hex_and_domain_rejected(self):
        bad_color = self.client.patch(
            WORKSPACE_URL,
            {"brand_color": "red"},
            format="json",
        )
        self.assertEqual(bad_color.status_code, status.HTTP_400_BAD_REQUEST)

        bad_domain = self.client.patch(
            WORKSPACE_URL,
            {"custom_domain": "https://evil.example/path"},
            format="json",
        )
        self.assertEqual(bad_domain.status_code, status.HTTP_400_BAD_REQUEST)

    def test_watermark_non_png_rejected(self):
        res = self.client.patch(
            WORKSPACE_URL,
            {"watermark_logo": _jpeg_upload()},
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_logo_png_accepted(self):
        res = self.client.patch(
            WORKSPACE_URL,
            {"logo": _png_upload()},
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertTrue(self.workspace.logo)

    def test_custom_domain_uniqueness_conflict(self):
        rival = User.objects.create_user(
            email="other@example.com",
            password="StrongPassword123!",
            name="Other",
            accepted_terms=True,
        )
        Workspace.objects.create(
            user=rival,
            business_name="Other Studio",
            custom_domain="taken.example",
        )
        res = self.client.patch(
            WORKSPACE_URL,
            {"custom_domain": "taken.example"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    @override_settings(CLOUDFLARE_WORKER_SHARED_SECRET="worker-secret")
    def test_custom_domain_patch_invalidates_resolve_cache(self):
        self.workspace.custom_domain = "old.studio.example"
        self.workspace.save(update_fields=["custom_domain"])
        self.assertEqual(
            get_workspace_id_by_domain("old.studio.example"),
            str(self.workspace.id),
        )

        res = self.client.patch(
            WORKSPACE_URL,
            {"custom_domain": "new.studio.example"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["custom_domain"], "new.studio.example")
        self.assertIsNone(get_workspace_id_by_domain("old.studio.example"))
        self.assertEqual(
            get_workspace_id_by_domain("new.studio.example"),
            str(self.workspace.id),
        )

    def test_empty_custom_domain_clears_field(self):
        self.workspace.custom_domain = "gone.studio.example"
        self.workspace.save(update_fields=["custom_domain"])
        res = self.client.patch(
            WORKSPACE_URL,
            {"custom_domain": ""},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertIsNone(self.workspace.custom_domain)

    def test_storage_fields_in_body_are_ignored(self):
        res = self.client.patch(
            WORKSPACE_URL,
            {"storage_used_bytes": 1, "storage_limit_bytes": 1},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 12345)
