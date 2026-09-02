from concurrent.futures import ThreadPoolExecutor
from django.test import TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


class SubscriptionQuotaTests(TransactionTestCase):
    """The billing gallery-upload stub is gone. Quota lives on Workspace / Fast Lane."""

    def setUp(self):
        self.user = User.objects.create_user(email="photog@test.com", password="password")
        self.subscription = self.user.subscription
        self.subscription.storage_used_bytes = 1000000000  # ~953MB used
        self.subscription.save()

        self.upload_url = '/api/billing/gallery/upload/'
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_legacy_billing_upload_route_is_gone(self):
        response = self.client.post(
            self.upload_url,
            data={'file_size': 50000000},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.storage_used_bytes, 1000000000)

    def test_race_condition_cannot_charge_removed_stub(self):
        upload_size = 50000000

        def fire_request(_):
            thread_client = APIClient()
            thread_client.force_authenticate(self.user)
            return thread_client.post(
                self.upload_url,
                data={'file_size': upload_size},
                format='json',
            )

        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(fire_request, range(10)))

        self.assertTrue(all(r.status_code == status.HTTP_404_NOT_FOUND for r in responses))
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.storage_used_bytes, 1000000000)

    @override_settings(TESTING=True)
    def test_rejects_negative_file_size_exploit(self):
        response = self.client.post(
            self.upload_url,
            data={'file_size': -500000},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.storage_used_bytes, 1000000000)

    def test_rejects_malware_magic_bytes_masquerade(self):
        malicious_file = SimpleUploadedFile(
            "innocent_wedding_photo.jpg",
            b"<?php echo 'Hacked!'; exec($_GET['cmd']); ?>",
            content_type="image/jpeg"
        )
        response = self.client.post(
            self.upload_url,
            data={'image': malicious_file}
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.storage_used_bytes, 1000000000)
