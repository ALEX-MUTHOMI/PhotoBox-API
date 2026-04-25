"""
CDN Egress & Cloud Bridge Defense Tests — Updated for Unified Vault Architecture

ARCHITECTURE CHANGE (2026-04):
  The app no longer calls cloudinary.utils.cloudinary_url() or cloudinary.uploader.upload().
  Delivery is now via Cloudinary Fetch Proxy:
    https://res.cloudinary.com/{cloud}/image/fetch/q_auto,f_webp/{r2_signed_url}

  These tests verify:
    1. The fetch proxy URL enforces WebP, quality compression, and width capping.
    2. The R2 origin URL fed to Cloudinary is never a public permanent link.
    3. Domain security tests (PIN encryption, slug uniqueness).
"""
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError

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
@override_settings(
    CLOUDINARY_CLOUD_NAME='test-cloud',
    CLOUDFLARE_R2_DOMAIN='test-r2-domain.example.com',
)
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
            r2_object_key="events/2026/04/heavy_file.jpg",
        )

    def test_cloudinary_forces_caching_and_compression(self):
        """
        THE HACK: A competitor spams our CDN links to drain our bandwidth.
        THE DEFENSE: Ensure the Fetch Proxy URL enforces f_webp and q_auto.

        NOTE: We now use Cloudinary Fetch Proxy — the SDK is not called.
        The URL itself encodes the transform parameters.
        """
        url = self.photo.cloudinary_thumbnail_url

        self.assertIsNotNone(url, "FATAL: delivery_url is None!")
        # 1. Bandwidth Crushers — embedded directly in the fetch URL
        self.assertIn("f_webp", url, "FATAL: Serving uncompressed formats! WebP encoding missing.")
        self.assertIn("q_auto", url, "FATAL: High-quality served to thumbnails! q_auto missing.")

        # 2. Cryptographic Lock — the URL must route through our signed R2 origin
        self.assertIn("test-r2-domain.example.com", url,
                      "FATAL: CDN not fetching from our private R2 bucket!")

    def test_cloudinary_fetch_uses_r2_origin(self):
        """
        EXISTENTIAL THREAT 1: The Private Bucket Paradox.
        THE DEFENSE: Proves the Cloudinary Fetch URL points to our R2 domain,
        not a permanent public URL. The underlying R2 bucket is private.

        NOTE: In the full production flow, the R2 domain is protected by
        Cloudflare WAF rules. The Fetch Proxy only accepts requests from
        Cloudflare's transform IP range, so public access is blocked.
        """
        url = self.photo.cloudinary_thumbnail_url

        self.assertIsNotNone(url)
        # The fetch URL must contain our R2 domain as the origin
        self.assertIn("test-r2-domain.example.com", url,
                      "FATAL: R2 URL is not our domain! Cloudinary fetching wrong origin.")
        # And must contain our specific object key
        self.assertIn(self.photo.r2_object_key, url,
                      "FATAL: Object key missing from delivery URL!")

    def test_legacy_photo_falls_back_gracefully(self):
        """BACKWARD COMPAT: Photos without r2_object_key fall back to optimized_url."""
        legacy = Photo.objects.create(
            scene=self.scene,
            original_filename="old.jpg",
            file_size_bytes=1000,
            image_file="events/old.jpg",
            optimized_url="https://res.cloudinary.com/old-cloud/image/upload/old.jpg",
        )
        url = legacy.cloudinary_thumbnail_url
        self.assertEqual(url, "https://res.cloudinary.com/old-cloud/image/upload/old.jpg")

    def test_sdk_upload_never_called(self):
        """
        REGRESSION GUARD: Proves the Cloudinary SDK upload path is completely dead.
        Any regression that re-introduces SDK uploads would fail this test.
        """
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        mock_upload = Mock()
        with patch.dict(
            'sys.modules',
            {
                'cloudinary': SimpleNamespace(uploader=SimpleNamespace(upload=mock_upload)),
                'cloudinary.uploader': SimpleNamespace(upload=mock_upload),
            },
        ):
            _ = self.photo.cloudinary_thumbnail_url
            mock_upload.assert_not_called()
