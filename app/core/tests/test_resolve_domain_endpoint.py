"""A3: cloudflare-workers/domain-router.js calls resolve-domain."""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import resolve, reverse

from core.models import Workspace

User = get_user_model()

WORKER_SECRET = "worker-shared-secret-for-tests"


@override_settings(CLOUDFLARE_WORKER_SHARED_SECRET=WORKER_SECRET)
class ResolveDomainEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="domain@example.com",
            password="StrongPassword123!",
            name="Domain",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Domain Studio",
        )
        self.workspace.custom_domain = "photos.studio.example"
        self.workspace.save(update_fields=["custom_domain"])

    def tearDown(self):
        cache.clear()

    def test_the_worker_url_path_resolves(self):
        match = resolve("/api/v1/core/resolve-domain/")
        self.assertIsNotNone(match)

    def test_route_is_reversible_by_name(self):
        self.assertEqual(
            reverse("core:resolve-domain"), "/api/v1/core/resolve-domain/"
        )

    def test_valid_worker_request_returns_the_workspace_id(self):
        response = self.client.get(
            "/api/v1/core/resolve-domain/",
            {"domain": "photos.studio.example"},
            HTTP_X_WORKER_SECRET=WORKER_SECRET,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workspace_id"], str(self.workspace.id))

    def test_unknown_domain_returns_200_with_an_empty_id(self):
        response = self.client.get(
            "/api/v1/core/resolve-domain/",
            {"domain": "nobody.example"},
            HTTP_X_WORKER_SECRET=WORKER_SECRET,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["workspace_id"], "")

    def test_missing_worker_secret_is_rejected(self):
        response = self.client.get(
            "/api/v1/core/resolve-domain/", {"domain": "photos.studio.example"}
        )
        self.assertEqual(response.status_code, 403)

    def test_wrong_worker_secret_is_rejected(self):
        response = self.client.get(
            "/api/v1/core/resolve-domain/",
            {"domain": "photos.studio.example"},
            HTTP_X_WORKER_SECRET="not-the-secret",
        )
        self.assertEqual(response.status_code, 403)

    def test_missing_domain_parameter_is_a_400(self):
        response = self.client.get(
            "/api/v1/core/resolve-domain/", HTTP_X_WORKER_SECRET=WORKER_SECRET
        )
        self.assertEqual(response.status_code, 400)

    def test_soft_deleted_workspace_does_not_resolve(self):
        # Warm the domain cache first — soft-delete must invalidate without
        # relying on a test-only cache.clear().
        warm = self.client.get(
            "/api/v1/core/resolve-domain/",
            {"domain": "photos.studio.example"},
            HTTP_X_WORKER_SECRET=WORKER_SECRET,
        )
        self.assertEqual(warm.json()["workspace_id"], str(self.workspace.id))

        self.workspace.is_deleted = True
        self.workspace.save(update_fields=["is_deleted"])

        response = self.client.get(
            "/api/v1/core/resolve-domain/",
            {"domain": "photos.studio.example"},
            HTTP_X_WORKER_SECRET=WORKER_SECRET,
        )
        self.assertEqual(response.json()["workspace_id"], "")


class DomainCacheInvalidationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="cache@example.com",
            password="StrongPassword123!",
            name="Cache",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Cache Studio",
            custom_domain="photos.studio.example",
        )

    def tearDown(self):
        cache.clear()

    def test_renaming_custom_domain_drops_cached_old_host(self):
        from core.domain_index import get_workspace_id_by_domain

        self.assertEqual(
            get_workspace_id_by_domain("photos.studio.example"),
            str(self.workspace.id),
        )

        self.workspace.custom_domain = "new.studio.example"
        self.workspace.save(update_fields=["custom_domain"])

        self.assertIsNone(get_workspace_id_by_domain("photos.studio.example"))
        self.assertEqual(
            get_workspace_id_by_domain("new.studio.example"),
            str(self.workspace.id),
        )

    def test_clearing_custom_domain_drops_cached_hosts(self):
        from core.domain_index import get_workspace_id_by_domain

        self.assertEqual(
            get_workspace_id_by_domain("photos.studio.example"),
            str(self.workspace.id),
        )

        self.workspace.custom_domain = None
        self.workspace.save(update_fields=["custom_domain"])

        self.assertIsNone(get_workspace_id_by_domain("photos.studio.example"))


class ResolveDomainMisconfigurationTests(TestCase):
    @override_settings(CLOUDFLARE_WORKER_SHARED_SECRET="")
    def test_unset_shared_secret_rejects_every_request(self):
        response = self.client.get(
            "/api/v1/core/resolve-domain/",
            {"domain": "anything.example"},
            HTTP_X_WORKER_SECRET="",
        )
        self.assertEqual(response.status_code, 503)
