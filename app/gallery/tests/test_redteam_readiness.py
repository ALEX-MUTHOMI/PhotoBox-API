"""Red-team tests for readiness-audit hardening (soft-delete, quota, purge, webhook)."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.domain_index import get_workspace_id_by_domain
from core.models import Workspace
from gallery.asset_purge import purge_photo_assets_task
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    issue_gallery_access_token,
)
from gallery.models import (
    Event,
    GalleryAccessRole,
    GalleryAccessSession,
    Photo,
    Scene,
)
from gallery.photo_failure import mark_photo_failed_and_release_quota

User = get_user_model()


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    FRONTEND_URL="https://app.photobox.test",
    SECURE_SSL_REDIRECT=False,
    CLOUDFLARE_R2_DOMAIN="cdn.example.test",
)
class SoftDeletePublicDoorMatrixTests(TestCase):
    """Every public share_code door must refuse soft-deleted workspaces."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="rt-soft@example.com",
            password="StrongPassword123!",
            name="RT Soft",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user, business_name="RT Studio"
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Wedding",
            slug="rt-soft-wed",
            is_published=True,
            allow_downloads=True,
        )
        self.gallery.set_pin("secret9")
        self.scene = Scene.objects.create(event=self.gallery, title="Ceremony")
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="a.jpg",
            status="READY",
            file_size_bytes=100,
            is_processed=True,
        )
        self.session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="guest@example.com",
            role=GalleryAccessRole.GUEST,
        )
        self.client_session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="client@example.com",
            role=GalleryAccessRole.CLIENT,
        )
        self.token = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email="guest@example.com",
            role=GalleryAccessRole.GUEST,
            pin_version=self.gallery.pin_version,
        )
        self.client_token = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email="client@example.com",
            role=GalleryAccessRole.CLIENT,
            pin_version=self.gallery.pin_version,
        )
        self.workspace.is_deleted = True
        self.workspace.save(update_fields=["is_deleted"])

    def _auth_cookies(self, *, client_role=False):
        if client_role:
            self.client.cookies["gallery_access"] = self.client_token
            self.client.cookies["gallery_session"] = encode_gallery_access_session_cookie(
                self.client_session.id
            )
        else:
            self.client.cookies["gallery_access"] = self.token
            self.client.cookies["gallery_session"] = encode_gallery_access_session_cookie(
                self.session.id
            )

    def test_guest_access_returns_404(self):
        url = reverse("gallery_public:guest-access", args=[self.gallery.share_code])
        res = self.client.post(url, {"pin": "secret9"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_magic_link_request_returns_404(self):
        url = reverse(
            "gallery_public:magic-link-request", args=[self.gallery.share_code]
        )
        res = self.client.post(url, {"email": "client@example.com"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_archive_request_returns_404(self):
        self._auth_cookies(client_role=True)
        url = reverse("gallery_public:archive-request", args=[self.gallery.share_code])
        res = self.client.post(url, {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_favorites_returns_404(self):
        self._auth_cookies()
        url = reverse("gallery_public:favorites", args=[self.gallery.share_code])
        res = self.client.post(
            url, {"photo_id": str(self.photo.id)}, format="json"
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(
    CLOUDFLARE_R2_DOMAIN="cdn.example.test",
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
)
class QuotaRefundRaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="rt-quota@example.com",
            password="StrongPassword123!",
            name="RT Quota",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Quota Studio",
            storage_used_bytes=2_000_000,
            storage_limit_bytes=50_000_000_000,
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Wedding",
            slug="rt-quota-wed",
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

    def test_destroy_of_failed_photo_does_not_refund_again(self):
        with patch("gallery.asset_purge.purge_photo_assets_task.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                mark_photo_failed_and_release_quota(self.photo.id)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 1_000_000)

        url = reverse("gallery:fastlane-photo-detail", args=[self.photo.id])
        with patch("gallery.asset_purge.purge_photo_assets_task.delay"):
            with self.captureOnCommitCallbacks(execute=True):
                res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.workspace.refresh_from_db()
        self.assertEqual(self.workspace.storage_used_bytes, 1_000_000)


class DomainSoftDeleteCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="rt-domain@example.com",
            password="StrongPassword123!",
            name="RT Domain",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Domain Studio",
            custom_domain="rt.studio.example",
        )

    def tearDown(self):
        cache.clear()

    def test_soft_delete_invalidates_warm_domain_cache_without_manual_clear(self):
        self.assertEqual(
            get_workspace_id_by_domain("rt.studio.example"),
            str(self.workspace.id),
        )
        self.workspace.is_deleted = True
        self.workspace.save(update_fields=["is_deleted"])
        self.assertIsNone(get_workspace_id_by_domain("rt.studio.example"))


class PurgeQueueAndLegacyWebhookTests(TestCase):
    def test_purge_task_is_on_default_queue(self):
        self.assertEqual(
            getattr(purge_photo_assets_task, "queue", None),
            "default",
        )

    @override_settings(ENABLE_LEGACY_R2_WEBHOOK=False)
    def test_legacy_webhook_disabled_returns_410(self):
        url = reverse("r2-webhook-ingress")
        res = self.client.post(url, data=b"{}", content_type="application/json")
        self.assertEqual(res.status_code, 410)


@override_settings(SECURE_SSL_REDIRECT=False)
class HealthProbeLeakTests(TestCase):
    def test_readiness_omits_debug_and_tracebacks(self):
        res = self.client.get(reverse("health-check"))
        self.assertEqual(res.status_code, 200)
        payload = res.json()
        self.assertNotIn("debug", payload)
        self.assertNotIn("traceback", payload)
        self.assertNotIn("exception", payload)
        body = res.content.decode("utf-8").lower()
        self.assertNotIn("traceback", body)

    def test_liveness_is_shallow_plaintext(self):
        res = self.client.get(reverse("health"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content.decode("utf-8"), "ok")
        self.assertNotIn(b"{", res.content)
