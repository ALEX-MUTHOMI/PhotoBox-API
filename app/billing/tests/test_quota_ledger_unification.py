"""Quota ledger unification: Workspace is the usage/limit source of truth."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace


User = get_user_model()


class BillingUploadStubRemovedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="quota@example.com",
            password="StrongPassword123!",
            name="Quota User",
            accepted_terms=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_legacy_billing_gallery_upload_route_is_gone(self):
        response = self.client.post(
            "/api/billing/gallery/upload/",
            {"file_size": 1024},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        if hasattr(self.user, "workspace"):
            workspace = Workspace.objects.get(user=self.user)
            self.assertEqual(workspace.storage_used_bytes, 0)
