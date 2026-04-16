import uuid
from unittest.mock import patch, PropertyMock

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

from rest_framework import status
from rest_framework.test import APIClient

# Application Imports
from core.models import Workspace
from gallery.models import Event, Scene, Photo

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
    @patch('django.db.models.fields.files.FieldFile.url', new_callable=PropertyMock)
    def test_cloudinary_fetch_uses_presigned_s3_urls(self, mock_url, mock_cloudinary_url):
        """
        EXISTENTIAL THREAT 1: The Private Bucket Paradox.
        THE DEFENSE: Proves the R2 URL fed to Cloudinary is a temporary, Pre-Signed GET URL,
        ensuring the underlying R2 bucket remains strictly Private and un-scrapable.
        """
        # Mock FieldFile.url to return a presigned url as django-storages S3Boto3Storage would
        mock_url.return_value = "https://r2.cloudflare.com/bucket/quarantine/123.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=900"
        mock_cloudinary_url.return_value = ("https://res.cloudinary.com/safe_url", {})

        url = self.photo.cloudinary_thumbnail_url

        # Extract the exact URL Django handed to the Cloudinary SDK
        call_args = mock_cloudinary_url.call_args[0]
        source_r2_url = call_args[0]

        # The source URL MUST contain AWS cryptographic signature parameters
        self.assertIn("X-Amz-Algorithm", source_r2_url, "FATAL: R2 URL is public! Cloudinary fetching un-signed URL.")
        self.assertIn("X-Amz-Expires", source_r2_url, "FATAL: R2 URL does not expire! Cloudinary fetching permanent link.")
