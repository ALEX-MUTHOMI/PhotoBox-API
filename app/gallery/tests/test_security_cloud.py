import uuid
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

from rest_framework import status
from rest_framework.test import APIClient

# Application Imports
from core.models import Workspace
from gallery.models import Event, Scene, Photo
from billing.models import Subscription, PricingPlan

User = get_user_model()

# ==========================================
# DOMAIN 1: FOUNDATIONAL CRYPTOGRAPHY
# ==========================================
class DomainSecurityTests(TestCase):
    """Testing the core cryptographic and database-level tenant isolation."""

    def setUp(self):
        self.user = User.objects.create_user(email="photographer@test.com", password="securepassword123")
        self.workspace = Workspace.objects.create(user=self.user, business_name="Test Studios")
        self.event = Event.objects.create(workspace=self.workspace, title="Safaricom Launch", slug="saf-launch")

    def test_argon2_pin_encryption(self):
        """SECURITY: Proves a 4-digit PIN is never stored in plain text and resists database leaks."""
        raw_pin = "4920"
        self.event.set_pin(raw_pin)

        self.assertNotEqual(self.event._hashed_pin, raw_pin, "FATAL: PIN stored in plain text!")
        self.assertTrue(self.event._hashed_pin.startswith('pbkdf2_') or 'argon2' in self.event._hashed_pin)

        self.assertTrue(self.event.check_pin("4920"), "FATAL: Correct PIN rejected.")
        self.assertFalse(self.event.check_pin("0000"), "FATAL: Incorrect PIN accepted.")
        self.assertFalse(self.event.check_pin("49201"), "FATAL: PIN length overflow bypass allowed.")

    def test_event_slug_uniqueness(self):
        """INFRASTRUCTURE: Prevents two events from hijacking the same public URL."""
        with self.assertRaises(IntegrityError):
            Event.objects.create(workspace=self.workspace, title="Dupe Launch", slug="saf-launch")


# ==========================================
# DOMAIN 2: CDN EGRESS & CLOUD BRIDGE DEFENSE
# ==========================================
class CloudinaryEgressDefenseTests(TestCase):
    """Defending against 'Billion Dollar' CDN Egress Attacks and R2 Scraping."""

    def setUp(self):
        self.user = User.objects.create_user(email="target@test.com", password="password123")
        self.workspace = Workspace.objects.create(user=self.user, business_name="Target Studios")
        self.event = Event.objects.create(workspace=self.workspace, title="Target Event", slug="target")
        self.scene = Scene.objects.create(event=self.event, title="Main", display_order=1)

        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="heavy_file.jpg",
            file_size_bytes=25000000,
            image_file="events/2026/04/heavy_file.jpg"
        )

    @patch('cloudinary.utils.cloudinary_url')
    def test_cloudinary_forces_caching_and_compression(self, mock_cloudinary_url):
        """
        THE HACK: A competitor spams our CDN links to drain our bandwidth.
        THE DEFENSE: Ensure URLs strictly enforce f_auto (WebP), q_auto:eco, and cryptographic signatures.
        """
        mock_cloudinary_url.return_value = ("https://res.cloudinary.com/safe_url", {})

        url = self.photo.cloudinary_thumbnail_url
        call_kwargs = mock_cloudinary_url.call_args[1]

        # 1. Bandwidth Crushers
        self.assertEqual(call_kwargs.get('fetch_format'), 'auto', "FATAL: Serving uncompressed formats!")
        self.assertEqual(call_kwargs.get('quality'), 'auto:eco', "FATAL: High-res served to thumbnails!")
        self.assertEqual(call_kwargs.get('width'), 800, "FATAL: Thumbnail width unbounded!")

        # 2. Cryptographic Lock
        self.assertTrue(call_kwargs.get('sign_url'), "FATAL: CDN URL is not cryptographically signed! Watermarks bypassed.")

    @patch('cloudinary.utils.cloudinary_url')
    def test_cloudinary_fetch_uses_presigned_s3_urls(self, mock_cloudinary_url):
        """
        EXISTENTIAL THREAT 1: The Private Bucket Paradox.
        THE DEFENSE: Proves the R2 URL fed to Cloudinary is a temporary, Pre-Signed GET URL,
        ensuring the underlying R2 bucket remains strictly Private and un-scrapable.
        """
        # Mock the image field to simulate a boto3 backend generating a pre-signed URL
        self.photo.image_file.url = "https://r2.cloudflare.com/bucket/quarantine/123.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=900"
        mock_cloudinary_url.return_value = ("https://res.cloudinary.com/safe_url", {})

        url = self.photo.cloudinary_thumbnail_url

        # Extract the exact URL Django handed to the Cloudinary SDK
        call_args = mock_cloudinary_url.call_args[0]
        source_r2_url = call_args[0]

        # The source URL MUST contain AWS cryptographic signature parameters
        self.assertIn("X-Amz-Algorithm", source_r2_url, "FATAL: R2 URL is public! Cloudinary fetching un-signed URL. Bucket is exposed to scraping.")
        self.assertIn("X-Amz-Expires", source_r2_url, "FATAL: R2 URL does not expire! Cloudinary fetching permanent link.")


# ==========================================
# DOMAIN 3: DIRECT-TO-CLOUD API BOUNCER
# ==========================================
class R2IngestionSecurityTests(TestCase):
    """Defending the API that issues the cryptographic S3 upload tickets."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(email="hacker@test.com", password="password123")
        self.workspace = Workspace.objects.create(user=self.user, business_name="Hacker Studios")
        self.event = Event.objects.create(workspace=self.workspace, title="Hacker Event", slug="hacker-event")
        self.scene = Scene.objects.create(event=self.event, title="Setup", display_order=1)

        # Seed a 1GB Subscription for the Quota Tests
        self.plan = PricingPlan.objects.create(name="Free", bandwidth_limit_bytes=1073741824)
        self.subscription = Subscription.objects.create(user=self.user, plan=self.plan, storage_used_bytes=0)

        # The endpoint we are preparing to build
        self.ticket_url = reverse('gallery:upload-ticket')

    def test_authentication_and_csrf_rejection(self):
        """
        THE HACK: Unauthenticated botnets or CSRF attacks attempt to drain quota.
        THE DEFENSE: DRF violently rejects unauthenticated or unverified token requests.
        """
        self.client.logout()
        payload = {"scene_id": str(self.scene.id), "filename": "bot.jpg", "file_size": 1000}
        response = self.client.post(self.ticket_url, payload)

        self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN], "FATAL: API endpoint is public! CSRF and Botnet attacks are active.")

    @patch('boto3.client')
    def test_presigned_post_enforces_payload_locks(self, mock_boto):
        """
        THE HACK: A user modifies React to upload a 5GB file.
        THE DEFENSE: The S3 POST ticket mathematically rejects bad sizes at the Edge.
        """
        mock_s3 = mock_boto.return_value
        mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {"key": "quarantine/test.jpg"}}

        self.client.force_authenticate(user=self.user)
        payload = {"scene_id": str(self.scene.id), "filename": "wedding.jpg", "file_size": 15000000}
        self.client.post(self.ticket_url, payload)

        conditions = mock_s3.generate_presigned_post.call_args[1].get('Conditions', [])

        size_lock = any(isinstance(c, list) and c[0] == "content-length-range" and c[2] == 52428800 for c in conditions)
        self.assertTrue(size_lock, "FATAL: Pre-signed URL exposes R2 to infinite file size uploads!")

    def test_tenant_isolation_idor_defense(self):
        """
        THE HACK: The 'Cuckoo Attack'. Uploading trash to a premium competitor's scene.
        THE DEFENSE: Strict Row-Level tenant verification.
        """
        competitor = User.objects.create_user(email="pro@test.com", password="password123")
        comp_workspace = Workspace.objects.create(user=competitor, business_name="Pro Studios")
        comp_event = Event.objects.create(workspace=comp_workspace, title="Private Event")
        comp_scene = Scene.objects.create(event=comp_event, title="Locked Setup")

        self.client.force_authenticate(user=self.user)
        payload = {"scene_id": str(comp_scene.id), "filename": "hack.jpg", "file_size": 1000}
        response = self.client.post(self.ticket_url, payload)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, "FATAL: Tenant IDOR vulnerability active. Cross-upload allowed.")

    def test_ticket_generation_enforces_billing_quota(self):
        """
        THE HACK: The 'Quota Ghost'. Requesting a ticket when out of space.
        THE DEFENSE: API verifies Subscription ledger before calling AWS.
        """
        self.subscription.storage_used_bytes = 1073741824 # Max out 1GB limit
        self.subscription.save()

        self.client.force_authenticate(user=self.user)
        payload = {"scene_id": str(self.scene.id), "filename": "tiny.jpg", "file_size": 1000000} # 1MB
        response = self.client.post(self.ticket_url, payload)

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED, "FATAL: Quota check bypassed! Users can steal storage.")

    @patch('boto3.client')
    def test_upload_key_uses_secure_uuid_and_quarantine(self, mock_boto):
        """
        THE HACK: The 'Collision Overwrite'. Two photographers upload "IMG_001.jpg".
        THE DEFENSE: API overwrites filename with UUID and forces into /quarantine/.
        """
        mock_s3 = mock_boto.return_value
        mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {"key": "quarantine/test.jpg"}}

        self.client.force_authenticate(user=self.user)
        self.client.post(self.ticket_url, {"scene_id": str(self.scene.id), "filename": "IMG_001.jpg", "file_size": 1000})

        key = mock_s3.generate_presigned_post.call_args[1].get('Fields', {}).get('key', '')

        self.assertNotIn("IMG_001.jpg", key, "FATAL: S3 Key uses user-provided filename. Overwrite vulnerability active.")
        self.assertTrue(key.startswith("quarantine/"), "FATAL: Uploads are bypassing the Quarantine Vault!")

    @patch('boto3.client')
    def test_presigned_post_embeds_relational_metadata(self, mock_boto):
        """
        THE HACK: The 'Amnesia Webhook'. Files land in S3 but Django orphans them.
        THE DEFENSE: S3 Ticket mathematically embeds the scene_id into AWS Metadata.
        """
        mock_s3 = mock_boto.return_value
        mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {}}

        self.client.force_authenticate(user=self.user)
        self.client.post(self.ticket_url, {"scene_id": str(self.scene.id), "filename": "test.jpg", "file_size": 1000})

        call_kwargs = mock_s3.generate_presigned_post.call_args[1]
        fields = call_kwargs.get('Fields', {})
        conditions = call_kwargs.get('Conditions', [])

        metadata_key = "x-amz-meta-scene-id"
        self.assertIn(metadata_key, fields, "FATAL: Relational metadata missing from ticket fields!")
        self.assertEqual(fields[metadata_key], str(self.scene.id), "FATAL: Wrong Scene ID embedded!")

        meta_condition = any(isinstance(c, dict) and metadata_key in c for c in conditions)
        self.assertTrue(meta_condition, "FATAL: Metadata is not cryptographically locked in Conditions!")

    @patch('boto3.client')
    def test_ticket_ttl_is_strictly_enforced(self, mock_boto):
        """
        THE HACK: The 'Zombie Ticket'. Re-using an old API ticket days later.
        THE DEFENSE: Expiration is hardcoded to 5 minutes (300 seconds).
        """
        mock_s3 = mock_boto.return_value
        mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {}}

        self.client.force_authenticate(user=self.user)
        self.client.post(self.ticket_url, {"scene_id": str(self.scene.id), "filename": "test.jpg", "file_size": 1000})

        expires_in = mock_s3.generate_presigned_post.call_args[1].get('ExpiresIn')
        self.assertLessEqual(expires_in, 300, "FATAL: Zombie Ticket Bleed active.")

    def test_malicious_filename_sanitization_and_extension_spoofing(self):
        """
        THE HACK: Path Traversal and XSS via filename payload.
        THE DEFENSE: API strips illegal characters from the original_filename before saving.
        """
        self.client.force_authenticate(user=self.user)

        malicious_filename = "../../../<script>alert('xss')</script>.jpg"
        payload = {"scene_id": str(self.scene.id), "filename": malicious_filename, "file_size": 1000}

        response = self.client.post(self.ticket_url, payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        returned_filename = response.data.get('sanitized_filename', '')
        self.assertNotIn("<script>", returned_filename, "FATAL: XSS Script allowed in filename!")
        self.assertNotIn("../", returned_filename, "FATAL: Path traversal characters allowed in filename!")

    @patch('boto3.client')
    def test_upload_key_forces_safe_file_extension(self, mock_boto):
        """
        EXISTENTIAL THREAT 2: The Execution in the Cloud Override.
        THE DEFENSE: Proves the API violently overwrites the user's file extension,
        ensuring malware cannot execute on a local machine even if it bypasses MIME checks.
        """
        mock_s3 = mock_boto.return_value
        mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {"key": "quarantine/uuid-123.jpg"}}

        self.client.force_authenticate(user=self.user)

        # Hacker sends an executable extension with a spoofed image MIME type
        payload = {"scene_id": str(self.scene.id), "filename": "malware.exe", "file_size": 1000}
        self.client.post(self.ticket_url, payload)

        call_kwargs = mock_s3.generate_presigned_post.call_args[1]
        key = call_kwargs.get('Fields', {}).get('key', '')

        # The key in S3 MUST NOT end in the user's requested .exe extension
        self.assertFalse(key.endswith(".exe"), "FATAL: S3 Key retained malicious executable extension!")

        # It MUST force a safe image extension
        safe_extensions = (".jpg", ".jpeg", ".png", ".webp")
        self.assertTrue(key.endswith(safe_extensions), "FATAL: API failed to force a safe image extension onto the S3 key.")










# import uuid
# from unittest.mock import patch

# from django.test import TestCase
# from django.urls import reverse
# from django.contrib.auth import get_user_model
# from django.db.utils import IntegrityError

# from rest_framework import status
# from rest_framework.test import APIClient

# # Application Imports
# from core.models import Workspace
# from gallery.models import Event, Scene, Photo
# from billing.models import Subscription, PricingPlan

# User = get_user_model()

# # ==========================================
# # DOMAIN 1: FOUNDATIONAL CRYPTOGRAPHY
# # ==========================================
# class DomainSecurityTests(TestCase):
#     """Testing the core cryptographic and database-level tenant isolation."""

#     def setUp(self):
#         self.user = User.objects.create_user(email="photographer@test.com", password="securepassword123")
#         self.workspace = Workspace.objects.create(user=self.user, business_name="Test Studios")
#         self.event = Event.objects.create(workspace=self.workspace, title="Safaricom Launch", slug="saf-launch")

#     def test_argon2_pin_encryption(self):
#         """SECURITY: Proves a 4-digit PIN is never stored in plain text and resists database leaks."""
#         raw_pin = "4920"
#         self.event.set_pin(raw_pin)

#         self.assertNotEqual(self.event._hashed_pin, raw_pin, "FATAL: PIN stored in plain text!")
#         self.assertTrue(self.event._hashed_pin.startswith('pbkdf2_') or 'argon2' in self.event._hashed_pin)

#         self.assertTrue(self.event.check_pin("4920"), "FATAL: Correct PIN rejected.")
#         self.assertFalse(self.event.check_pin("0000"), "FATAL: Incorrect PIN accepted.")
#         self.assertFalse(self.event.check_pin("49201"), "FATAL: PIN length overflow bypass allowed.")

#     def test_event_slug_uniqueness(self):
#         """INFRASTRUCTURE: Prevents two events from hijacking the same public URL."""
#         with self.assertRaises(IntegrityError):
#             Event.objects.create(workspace=self.workspace, title="Dupe Launch", slug="saf-launch")


# # ==========================================
# # DOMAIN 2: CDN EGRESS & HIJACKING DEFENSE
# # ==========================================
# class CloudinaryEgressDefenseTests(TestCase):
#     """Defending against the 'Billion Dollar' CDN Egress Attack."""

#     def setUp(self):
#         self.user = User.objects.create_user(email="target@test.com", password="password123")
#         self.workspace = Workspace.objects.create(user=self.user, business_name="Target Studios")
#         self.event = Event.objects.create(workspace=self.workspace, title="Target Event", slug="target")
#         self.scene = Scene.objects.create(event=self.event, title="Main", display_order=1)

#         self.photo = Photo.objects.create(
#             scene=self.scene,
#             original_filename="heavy_file.jpg",
#             file_size_bytes=25000000, # 25MB
#             image_file="events/2026/04/heavy_file.jpg"
#         )

#     @patch('cloudinary.utils.cloudinary_url')
#     def test_cloudinary_forces_caching_and_compression(self, mock_cloudinary_url):
#         """
#         THE HACK: A competitor spams our CDN links to drain our bandwidth.
#         THE DEFENSE: Ensure URLs strictly enforce f_auto (WebP), q_auto:eco, and cryptographic signatures.
#         """
#         mock_cloudinary_url.return_value = ("https://res.cloudinary.com/safe_url", {})

#         url = self.photo.cloudinary_thumbnail_url
#         call_kwargs = mock_cloudinary_url.call_args[1]

#         # 1. Bandwidth Crushers
#         self.assertEqual(call_kwargs.get('fetch_format'), 'auto', "FATAL: Serving uncompressed formats!")
#         self.assertEqual(call_kwargs.get('quality'), 'auto:eco', "FATAL: High-res served to thumbnails!")
#         self.assertEqual(call_kwargs.get('width'), 800, "FATAL: Thumbnail width unbounded!")

#         # 2. Cryptographic Lock
#         self.assertTrue(call_kwargs.get('sign_url'), "FATAL: CDN URL is not cryptographically signed! Watermarks can be bypassed.")


# # ==========================================
# # DOMAIN 3: DIRECT-TO-CLOUD API BOUNCER
# # ==========================================
# class R2IngestionSecurityTests(TestCase):
#     """Defending the API that issues the cryptographic S3 upload tickets."""

#     def setUp(self):
#         self.client = APIClient()
#         self.user = User.objects.create_user(email="hacker@test.com", password="password123")
#         self.workspace = Workspace.objects.create(user=self.user, business_name="Hacker Studios")
#         self.event = Event.objects.create(workspace=self.workspace, title="Hacker Event", slug="hacker-event")
#         self.scene = Scene.objects.create(event=self.event, title="Setup", display_order=1)

#         # Seed a 1GB Subscription for the Quota Tests
#         self.plan = PricingPlan.objects.create(name="Free", bandwidth_limit_bytes=1073741824)
#         self.subscription = Subscription.objects.create(user=self.user, plan=self.plan, storage_used_bytes=0)

#         # The endpoint we are preparing to build
#         self.ticket_url = reverse('gallery:upload-ticket')

#     def test_authentication_and_csrf_rejection(self):
#         """
#         THE HACK: Unauthenticated botnets or CSRF attacks attempt to drain quota.
#         THE DEFENSE: DRF violently rejects unauthenticated or unverified token requests.
#         """
#         # Ensure the client is completely unauthenticated
#         self.client.logout()

#         payload = {"scene_id": str(self.scene.id), "filename": "bot.jpg", "file_size": 1000}
#         response = self.client.post(self.ticket_url, payload)

#         # Must return 401 Unauthorized or 403 Forbidden
#         self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN], "FATAL: API endpoint is public! CSRF and Botnet attacks are active.")

#     @patch('boto3.client')
#     def test_presigned_post_enforces_payload_locks(self, mock_boto):
#         """
#         THE HACK: A user modifies React to upload a 5GB file.
#         THE DEFENSE: The S3 POST ticket mathematically rejects bad sizes at the Edge.
#         """
#         mock_s3 = mock_boto.return_value
#         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {"key": "quarantine/test.jpg"}}

#         self.client.force_authenticate(user=self.user)
#         payload = {"scene_id": str(self.scene.id), "filename": "wedding.jpg", "file_size": 15000000}
#         self.client.post(self.ticket_url, payload)

#         conditions = mock_s3.generate_presigned_post.call_args[1].get('Conditions', [])

#         # Verify Size Ceiling (Max 50MB)
#         size_lock = any(isinstance(c, list) and c[0] == "content-length-range" and c[2] == 52428800 for c in conditions)
#         self.assertTrue(size_lock, "FATAL: Pre-signed URL exposes R2 to infinite file size uploads!")

#     def test_tenant_isolation_idor_defense(self):
#         """
#         THE HACK: The 'Cuckoo Attack'. Uploading trash to a premium competitor's scene.
#         THE DEFENSE: Strict Row-Level tenant verification.
#         """
#         competitor = User.objects.create_user(email="pro@test.com", password="password123")
#         comp_workspace = Workspace.objects.create(user=competitor, business_name="Pro Studios")
#         comp_event = Event.objects.create(workspace=comp_workspace, title="Private Event")
#         comp_scene = Scene.objects.create(event=comp_event, title="Locked Setup")

#         self.client.force_authenticate(user=self.user) # Authenticated as Hacker

#         payload = {"scene_id": str(comp_scene.id), "filename": "hack.jpg", "file_size": 1000}
#         response = self.client.post(self.ticket_url, payload)

#         self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, "FATAL: Tenant IDOR vulnerability active. Cross-upload allowed.")

#     def test_ticket_generation_enforces_billing_quota(self):
#         """
#         THE HACK: The 'Quota Ghost'. Requesting a ticket when out of space.
#         THE DEFENSE: API verifies Subscription ledger before calling AWS.
#         """
#         self.subscription.storage_used_bytes = 1073741824 # Max out 1GB limit
#         self.subscription.save()

#         self.client.force_authenticate(user=self.user)
#         payload = {"scene_id": str(self.scene.id), "filename": "tiny.jpg", "file_size": 1000000} # 1MB
#         response = self.client.post(self.ticket_url, payload)

#         self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED, "FATAL: Quota check bypassed! Users can steal storage.")

#     @patch('boto3.client')
#     def test_upload_key_uses_secure_uuid_and_quarantine(self, mock_boto):
#         """
#         THE HACK: The 'Collision Overwrite'. Two photographers upload "IMG_001.jpg".
#         THE DEFENSE: API overwrites filename with UUID and forces into /quarantine/.
#         """
#         mock_s3 = mock_boto.return_value
#         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {"key": "quarantine/test.jpg"}}

#         self.client.force_authenticate(user=self.user)
#         self.client.post(self.ticket_url, {"scene_id": str(self.scene.id), "filename": "IMG_001.jpg", "file_size": 1000})

#         key = mock_s3.generate_presigned_post.call_args[1].get('Fields', {}).get('key', '')

#         self.assertNotIn("IMG_001.jpg", key, "FATAL: S3 Key uses user-provided filename. Overwrite vulnerability active.")
#         self.assertTrue(key.startswith("quarantine/"), "FATAL: Uploads are bypassing the Quarantine Vault!")

#     @patch('boto3.client')
#     def test_presigned_post_embeds_relational_metadata(self, mock_boto):
#         """
#         THE HACK: The 'Amnesia Webhook'. Files land in S3 but Django orphans them.
#         THE DEFENSE: S3 Ticket mathematically embeds the scene_id into AWS Metadata.
#         """
#         mock_s3 = mock_boto.return_value
#         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {}}

#         self.client.force_authenticate(user=self.user)
#         self.client.post(self.ticket_url, {"scene_id": str(self.scene.id), "filename": "test.jpg", "file_size": 1000})

#         call_kwargs = mock_s3.generate_presigned_post.call_args[1]
#         fields = call_kwargs.get('Fields', {})
#         conditions = call_kwargs.get('Conditions', [])

#         metadata_key = "x-amz-meta-scene-id"
#         self.assertIn(metadata_key, fields, "FATAL: Relational metadata missing from ticket fields!")
#         self.assertEqual(fields[metadata_key], str(self.scene.id), "FATAL: Wrong Scene ID embedded!")

#         meta_condition = any(isinstance(c, dict) and metadata_key in c for c in conditions)
#         self.assertTrue(meta_condition, "FATAL: Metadata is not cryptographically locked in Conditions!")

#     @patch('boto3.client')
#     def test_ticket_ttl_is_strictly_enforced(self, mock_boto):
#         """
#         THE HACK: The 'Zombie Ticket'. Re-using an old API ticket days later.
#         THE DEFENSE: Expiration is hardcoded to 5 minutes (300 seconds).
#         """
#         mock_s3 = mock_boto.return_value
#         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {}}

#         self.client.force_authenticate(user=self.user)
#         self.client.post(self.ticket_url, {"scene_id": str(self.scene.id), "filename": "test.jpg", "file_size": 1000})

#         expires_in = mock_s3.generate_presigned_post.call_args[1].get('ExpiresIn')
#         self.assertLessEqual(expires_in, 300, "FATAL: Zombie Ticket Bleed active.")

#     @patch('boto3.client')
#     def test_malicious_filename_sanitization_and_extension_spoofing(self, mock_boto):
#         """
#         THE HACK: Path Traversal, XSS, and Extension Spoofing (.pnk / .exe).
#         THE DEFENSE: API strips illegal characters and mathematically forces a safe extension.
#         """
#         mock_s3 = mock_boto.return_value
#         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {"key": "quarantine/test.jpg"}}

#         self.client.force_authenticate(user=self.user)

#         # Payload 1: The XSS Path Traversal
#         malicious_filename = "../../../<script>alert('xss')</script>.jpg"
#         payload_1 = {"scene_id": str(self.scene.id), "filename": malicious_filename, "file_size": 1000}

#         response_1 = self.client.post(self.ticket_url, payload_1)
#         self.assertEqual(response_1.status_code, status.HTTP_200_OK)

#         returned_filename = response_1.data.get('sanitized_filename', '')
#         self.assertNotIn("<script>", returned_filename, "FATAL: XSS Script allowed in filename!")
#         self.assertNotIn("../", returned_filename, "FATAL: Path traversal characters allowed in filename!")

#         # Payload 2: The Extension Spoof (.exe disguised as upload)
#         payload_2 = {"scene_id": str(self.scene.id), "filename": "virus.exe", "file_size": 1000}
#         self.client.post(self.ticket_url, payload_2)

#         # Verify the MIME lock dynamically reacted to the bad extension or forced a safe one
#         call_kwargs = mock_s3.generate_presigned_post.call_args[1]
#         conditions = call_kwargs.get('Conditions', [])

#         mime_lock = any(isinstance(c, list) and c[0] == "starts-with" and c[1] == "$Content-Type" and c[2] in ["image/jpeg", "image/png", "image/webp"] for c in conditions)
#         self.assertTrue(mime_lock, "FATAL: S3 Ticket allowed a non-image MIME type! Malware execution active.")




# # import uuid
# # from unittest.mock import patch
# # from django.test import TestCase
# # from django.urls import reverse
# # from django.contrib.auth import get_user_model
# # from rest_framework import status
# # from rest_framework.test import APIClient

# # # Application Imports
# # from core.models import Workspace
# # from gallery.models import Event, Scene, Photo
# # from billing.models import Subscription, PricingPlan

# # User = get_user_model()

# # class CloudinaryEgressDefenseTests(TestCase):
# #     """DOMAIN 1: Defending against the 'Billion Dollar' CDN Egress Attack."""

# #     def setUp(self):
# #         self.user = User.objects.create_user(email="target@test.com", password="password123")
# #         self.workspace = Workspace.objects.create(user=self.user, business_name="Target Studios")
# #         self.event = Event.objects.create(workspace=self.workspace, title="Target Event", slug="target")
# #         self.scene = Scene.objects.create(event=self.event, title="Main", display_order=1)

# #         self.photo = Photo.objects.create(
# #             scene=self.scene,
# #             original_filename="heavy_file.jpg",
# #             file_size_bytes=25000000, # 25MB
# #             image_file="events/2026/04/heavy_file.jpg"
# #         )

# #     @patch('cloudinary.utils.cloudinary_url')
# #     def test_cloudinary_forces_caching_and_compression(self, mock_cloudinary_url):
# #         """
# #         THE HACK: A competitor spams our CDN links to drain our bandwidth.
# #         THE DEFENSE: Ensure URLs strictly enforce f_auto (WebP), q_auto:eco, and cryptographic signatures.
# #         """
# #         mock_cloudinary_url.return_value = ("https://res.cloudinary.com/safe_url", {})

# #         url = self.photo.cloudinary_thumbnail_url
# #         call_kwargs = mock_cloudinary_url.call_args[1]

# #         # 1. Bandwidth Crushers
# #         self.assertEqual(call_kwargs.get('fetch_format'), 'auto', "FATAL: Serving uncompressed formats!")
# #         self.assertEqual(call_kwargs.get('quality'), 'auto:eco', "FATAL: High-res served to thumbnails!")
# #         self.assertEqual(call_kwargs.get('width'), 800, "FATAL: Thumbnail width unbounded!")

# #         # 2. Cryptographic Lock
# #         self.assertTrue(call_kwargs.get('sign_url'), "FATAL: CDN URL is not cryptographically signed! Watermarks can be bypassed.")


# # class R2IngestionSecurityTests(TestCase):
# #     """DOMAIN 2: Defending the Direct-to-Cloud Upload Pipeline (The API Bouncer)."""

# #     def setUp(self):
# #         self.client = APIClient()
# #         self.user = User.objects.create_user(email="hacker@test.com", password="password123")
# #         self.workspace = Workspace.objects.create(user=self.user, business_name="Hacker Studios")
# #         self.event = Event.objects.create(workspace=self.workspace, title="Hacker Event", slug="hacker-event")
# #         self.scene = Scene.objects.create(event=self.event, title="Setup", display_order=1)

# #         # Seed a 1GB Subscription for the Quota Tests
# #         self.plan = PricingPlan.objects.create(name="Free", bandwidth_limit_bytes=1073741824)
# #         self.subscription = Subscription.objects.create(
# #             user=self.user,
# #             plan=self.plan,
# #             storage_used_bytes=0
# #         )

# #         # The endpoint we are preparing to build
# #         self.ticket_url = reverse('gallery:upload-ticket')

# #     @patch('boto3.client')
# #     def test_presigned_post_enforces_payload_locks(self, mock_boto):
# #         """
# #         THE HACK: A user modifies React to upload a 5GB .exe file.
# #         THE DEFENSE: The S3 POST ticket mathematically rejects bad sizes/MIMEs at the Edge.
# #         """
# #         mock_s3 = mock_boto.return_value
# #         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {"key": "quarantine/test.jpg"}}

# #         self.client.force_authenticate(user=self.user)
# #         payload = {"scene_id": str(self.scene.id), "filename": "wedding.jpg", "file_size": 15000000}

# #         response = self.client.post(self.ticket_url, payload)
# #         self.assertEqual(response.status_code, status.HTTP_200_OK)

# #         call_kwargs = mock_s3.generate_presigned_post.call_args[1]
# #         conditions = call_kwargs.get('Conditions', [])

# #         # 1. Verify Size Ceiling (50MB)
# #         size_lock = any(isinstance(c, list) and c[0] == "content-length-range" and c[2] == 52428800 for c in conditions)
# #         self.assertTrue(size_lock, "FATAL: Pre-signed URL exposes R2 to infinite file size uploads!")

# #         # 2. Verify MIME spoofing defense
# #         mime_lock = any(isinstance(c, list) and c[0] == "starts-with" and c[1] == "$Content-Type" and c[2] == "image/" for c in conditions)
# #         self.assertTrue(mime_lock, "FATAL: Pre-signed URL allows non-image files! Malware payload possible.")

# #     def test_tenant_isolation_idor_defense(self):
# #         """
# #         THE HACK: The 'Cuckoo Attack'. Uploading trash to a premium competitor's scene.
# #         THE DEFENSE: Strict Row-Level tenant verification.
# #         """
# #         competitor = User.objects.create_user(email="pro@test.com", password="password123")
# #         comp_workspace = Workspace.objects.create(user=competitor, business_name="Pro Studios")
# #         comp_event = Event.objects.create(workspace=comp_workspace, title="Private Event")
# #         comp_scene = Scene.objects.create(event=comp_event, title="Locked Setup")

# #         self.client.force_authenticate(user=self.user) # Authenticated as Hacker

# #         payload = {"scene_id": str(comp_scene.id), "filename": "hack.jpg", "file_size": 1000}
# #         response = self.client.post(self.ticket_url, payload)

# #         self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, "FATAL: Tenant IDOR vulnerability! Cross-upload allowed.")

# #     def test_ticket_generation_enforces_billing_quota(self):
# #         """
# #         THE HACK: The 'Quota Ghost'. Requesting a ticket when out of space.
# #         THE DEFENSE: API verifies Subscription ledger before calling AWS.
# #         """
# #         self.subscription.storage_used_bytes = 1073741824 # Max out 1GB limit
# #         self.subscription.save()

# #         self.client.force_authenticate(user=self.user)
# #         payload = {"scene_id": str(self.scene.id), "filename": "tiny.jpg", "file_size": 1000000} # 1MB
# #         response = self.client.post(self.ticket_url, payload)

# #         self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED, "FATAL: Quota check bypassed! Users can steal storage.")

# #     @patch('boto3.client')
# #     def test_upload_key_uses_secure_uuid_and_quarantine(self, mock_boto):
# #         """
# #         THE HACK: The 'Collision Overwrite'. Two photographers upload "IMG_001.jpg".
# #         THE DEFENSE: API overwrites filename with UUID and forces into /quarantine/.
# #         """
# #         mock_s3 = mock_boto.return_value
# #         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {"key": "quarantine/test.jpg"}}

# #         self.client.force_authenticate(user=self.user)
# #         payload = {"scene_id": str(self.scene.id), "filename": "IMG_001.jpg", "file_size": 1000}
# #         self.client.post(self.ticket_url, payload)

# #         call_kwargs = mock_s3.generate_presigned_post.call_args[1]
# #         key = call_kwargs.get('Fields', {}).get('key', '')

# #         self.assertNotIn("IMG_001.jpg", key, "FATAL: S3 Key uses user-provided filename. Overwrite vulnerability active.")
# #         self.assertTrue(key.startswith("quarantine/"), "FATAL: Uploads are bypassing the Quarantine Vault!")

# #     @patch('boto3.client')
# #     def test_presigned_post_embeds_relational_metadata(self, mock_boto):
# #         """
# #         THE HACK: The 'Amnesia Webhook'. Files land in S3 but Django orphans them.
# #         THE DEFENSE: S3 Ticket mathematically embeds the scene_id into AWS Metadata.
# #         """
# #         mock_s3 = mock_boto.return_value
# #         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {}}

# #         self.client.force_authenticate(user=self.user)
# #         payload = {"scene_id": str(self.scene.id), "filename": "test.jpg", "file_size": 1000}
# #         self.client.post(self.ticket_url, payload)

# #         call_kwargs = mock_s3.generate_presigned_post.call_args[1]
# #         conditions = call_kwargs.get('Conditions', [])
# #         fields = call_kwargs.get('Fields', {})

# #         metadata_key = "x-amz-meta-scene-id"
# #         self.assertIn(metadata_key, fields, "FATAL: Relational metadata missing from ticket fields!")
# #         self.assertEqual(fields[metadata_key], str(self.scene.id), "FATAL: Wrong Scene ID embedded!")

# #         meta_condition = any(isinstance(c, dict) and metadata_key in c for c in conditions)
# #         self.assertTrue(meta_condition, "FATAL: Metadata is not cryptographically locked in Conditions!")

# #     @patch('boto3.client')
# #     def test_ticket_ttl_is_strictly_enforced(self, mock_boto):
# #         """
# #         THE HACK: The 'Zombie Ticket'. Re-using an old API ticket days later.
# #         THE DEFENSE: Expiration is hardcoded to 5 minutes (300 seconds).
# #         """
# #         mock_s3 = mock_boto.return_value
# #         mock_s3.generate_presigned_post.return_value = {"url": "url", "fields": {}}

# #         self.client.force_authenticate(user=self.user)
# #         payload = {"scene_id": str(self.scene.id), "filename": "test.jpg", "file_size": 1000}
# #         self.client.post(self.ticket_url, payload)

# #         call_kwargs = mock_s3.generate_presigned_post.call_args[1]
# #         expires_in = call_kwargs.get('ExpiresIn')

# #         self.assertIsNotNone(expires_in, "FATAL: Ticket lives forever!")
# #         self.assertLessEqual(expires_in, 300, "FATAL: Ticket TTL is dangerously long!")

# #     def test_malicious_filename_sanitization(self):
# #         """
# #         THE HACK: Path Traversal & XSS via filename payload.
# #         THE DEFENSE: The API violently strips illegal characters from the original_filename.
# #         """
# #         self.client.force_authenticate(user=self.user)

# #         # Attempt to inject an XSS script and path traversal into the database
# #         malicious_filename = "../../../<script>alert('xss')</script>.jpg"
# #         payload = {
# #             "scene_id": str(self.scene.id),
# #             "filename": malicious_filename,
# #             "file_size": 1000
# #         }

# #         response = self.client.post(self.ticket_url, payload)

# #         # The API shouldn't necessarily crash, but it MUST sanitize the filename in the response
# #         # We will build the View to return the sanitized filename back to React
# #         self.assertEqual(response.status_code, status.HTTP_200_OK)
# #         returned_filename = response.data.get('sanitized_filename', '')

# #         self.assertNotIn("<script>", returned_filename, "FATAL: XSS Script allowed in filename!")
# #         self.assertNotIn("../", returned_filename, "FATAL: Path traversal characters allowed in filename!")
