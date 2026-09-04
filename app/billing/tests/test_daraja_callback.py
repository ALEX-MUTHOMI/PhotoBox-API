"""Phase D: Daraja secret_token + idempotency; Phase E: ZIP fail-closed."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from unittest.mock import patch

from billing.daraja import mint_daraja_callback_token
from billing.models import DarajaCallbackToken, ProcessedWebhook
from gallery.tasks import build_gallery_archive
from gallery.zip_lease import acquire_zip_lease


User = get_user_model()


class DarajaCallbackTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.client = APIClient()
        self.raw = mint_daraja_callback_token(user=self.user, checkout_request_id="ws_1")

    def test_missing_token_403(self):
        res = self.client.post(reverse("billing:daraja_callback"), {"ResultCode": 0}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_wrong_token_403(self):
        res = self.client.post(
            reverse("billing:daraja_callback") + "?secret_token=nope",
            {"ResultCode": 0, "CheckoutRequestID": "ws_1"},
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_valid_once_then_replay(self):
        url = reverse("billing:daraja_callback") + f"?secret_token={self.raw}"
        first = self.client.post(
            url,
            {"ResultCode": 0, "CheckoutRequestID": "ws_1"},
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(ProcessedWebhook.objects.count(), 1)
        second = self.client.post(
            url,
            {"ResultCode": 0, "CheckoutRequestID": "ws_1"},
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(DarajaCallbackToken.objects.get().used_at is not None, True)

    @override_settings(DARAJA_CALLBACK_IP_ALLOWLIST=["203.0.113.10"])
    def test_ip_outside_allowlist_403_even_with_valid_token(self):
        url = reverse("billing:daraja_callback") + f"?secret_token={self.raw}"
        res = self.client.post(
            url,
            {"ResultCode": 0, "CheckoutRequestID": "ws_1"},
            format="json",
            REMOTE_ADDR="198.51.100.1",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ProcessedWebhook.objects.count(), 0)

        allowed = self.client.post(
            url,
            {"ResultCode": 0, "CheckoutRequestID": "ws_1"},
            format="json",
            REMOTE_ADDR="203.0.113.10",
        )
        self.assertEqual(allowed.status_code, status.HTTP_202_ACCEPTED)


class ZipLeaseProdTests(TestCase):
    @override_settings(DEBUG=False)
    @patch("gallery.zip_lease._client", return_value=None)
    def test_zip_lease_fail_closed_without_redis_in_prod(self, _mock_client):
        decision = acquire_zip_lease("job-1", "gallery-1")
        self.assertFalse(decision.acquired)
        self.assertIn("fail_closed", decision.reason)

    def test_archive_task_bound_to_archive_zip_queue(self):
        self.assertEqual(build_gallery_archive.queue, "archive-zip")
