from concurrent.futures import ThreadPoolExecutor
from django.test import TransactionTestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from billing.models import Subscription
from rest_framework import status

User = get_user_model()

class SubscriptionQuotaTests(TransactionTestCase):
    """Simulates real-world attempts to breach the 1GB storage limit and bypass file filters."""

    def setUp(self):
        self.user = User.objects.create_user(email="photog@test.com", password="password")
        self.subscription = self.user.subscription
        self.subscription.storage_used_bytes = 1000000000 # ~953MB used
        self.subscription.save()

        # Updated to match the exact URL routing of your billing app
        self.upload_url = '/api/billing/gallery/upload/'
        self.client = Client()
        self.client.force_login(self.user)

    def test_race_condition_upload_defense(self):
        """
        HACKER: Fires 10 concurrent 50MB uploads to bypass the 1GB wall.
        DEFENSE: The View MUST use select_for_update() row-level locking.
        """
        upload_size = 50000000 # 50MB

        def fire_request(_):
            # THE ENGINEER FIX: Thread-safe isolated client for the botnet simulation
            thread_client = Client()
            thread_client.force_login(self.user)

            return thread_client.post(
                self.upload_url,
                data={'file_size': upload_size},
                content_type='application/json'
            )

        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(fire_request, range(10)))

        # Mathematical Certainty: Only 1 can fit (1000MB + 50MB = 1050MB < 1073MB)
        # The remaining 9 must be blocked with Payment Required (402).
        successes = sum(1 for r in responses if r.status_code == status.HTTP_201_CREATED)
        blocks = sum(1 for r in responses if r.status_code == status.HTTP_402_PAYMENT_REQUIRED)

        self.assertEqual(successes, 1)
        self.assertEqual(blocks, 9)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.storage_used_bytes, 1050000000)

    @override_settings(TESTING=True)
    def test_rejects_negative_file_size_exploit(self):
        """
        HACKER: Sends a negative file size to trick the server into refunding storage quota.
        DEFENSE: Server must reject actual_file_size <= 0.
        """
        response = self.client.post(
            self.upload_url,
            data={'file_size': -500000},
            content_type='application/json'
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Verify the database math was untouched
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.storage_used_bytes, 1000000000)

    def test_rejects_malware_magic_bytes_masquerade(self):
        """
        HACKER: Uploads a malicious PHP script renamed to end in .jpg.
        DEFENSE: The server ignores the extension and inspects the binary Magic Bytes.
        """
        # We create a file that claims to be a JPEG, but the raw binary is actually a PHP script.
        malicious_file = SimpleUploadedFile(
            "innocent_wedding_photo.jpg",
            b"<?php echo 'Hacked!'; exec($_GET['cmd']); ?>",
            content_type="image/jpeg"
        )

        response = self.client.post(
            self.upload_url,
            data={'image': malicious_file}
        )

        # The server must detect the payload mismatch and reject the media type
        self.assertEqual(response.status_code, status.HTTP_415_UNSUPPORTED_MEDIA_TYPE)

        # Verify the malicious file didn't trigger a storage deduction
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.storage_used_bytes, 1000000000)
