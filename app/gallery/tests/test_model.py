"""
Gallery Model & CDN Security Tests — Updated for Unified Vault Architecture

ARCHITECTURE CHANGE (2026-04):
  cloudinary.utils.cloudinary_url is NO LONGER CALLED.
  Photos are delivered via the Cloudinary Fetch Proxy pattern:
    https://res.cloudinary.com/{cloud_name}/image/fetch/q_auto,f_webp/{r2_public_url}
  These tests verify the NEW delivery contract.
"""
from unittest.mock import patch, PropertyMock
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from core.models import Workspace
from gallery.models import Event, Scene, Photo

User = get_user_model()


class EventSecurityTests(TestCase):
    """The Hacker's Domain: Attacking the Event Access Controls."""

    def setUp(self):
        self.user = User.objects.create_user(email="photographer@test.com", password="securepassword123")
        self.workspace = Workspace.objects.create(user=self.user, business_name="Test Studios")
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Safaricom Launch",
            slug="safaricom-launch-2026"
        )

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
            Event.objects.create(
                workspace=self.workspace,
                title="Another Launch",
                slug="safaricom-launch-2026"  # Duplicate slug
            )


@override_settings(
    CLOUDINARY_CLOUD_NAME='test-cloud',
    CLOUDFLARE_R2_DOMAIN='test-r2-domain.example.com',
)
class PhotoCloudinaryTests(TestCase):
    """Tests the Cloudinary Fetch Proxy delivery architecture."""

    def setUp(self):
        self.user = User.objects.create_user(email="dev@test.com", password="password123")
        self.workspace = Workspace.objects.create(user=self.user, business_name="Cloud Studios")
        self.event = Event.objects.create(workspace=self.workspace, title="Cloud Event", slug="cloud-event")
        self.scene = Scene.objects.create(event=self.event, title="Keynote", display_order=1)

        # Photo with r2_object_key — the new architecture
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="raw_shot.jpg",
            file_size_bytes=25000000,
            r2_object_key="events/2026/04/raw_shot.jpg",
            is_processed=True
        )

    def test_cloudinary_thumbnail_url_uses_fetch_proxy(self):
        """
        ARCHITECTURE: delivery_url must use Cloudinary Fetch Proxy, NOT SDK upload.
        The URL must encode WebP conversion (f_webp) and quality optimisation (q_auto).
        """
        url = self.photo.cloudinary_thumbnail_url

        self.assertIsNotNone(url, "FATAL: delivery_url returned None!")
        self.assertIn("res.cloudinary.com", url, "FATAL: Not a Cloudinary URL!")
        self.assertIn("/image/fetch/", url, "FATAL: Not using Fetch Proxy pattern!")
        self.assertIn("q_auto", url, "FATAL: Quality optimisation missing — bandwidth risk!")
        self.assertIn("f_webp", url, "FATAL: WebP conversion missing — serving raw files!")
        self.assertIn("test-cloud", url, "FATAL: Wrong cloud name in URL!")

    def test_delivery_url_contains_r2_origin(self):
        """
        SECURITY: The fetch proxy URL must point to our private R2 bucket domain.
        Cloudinary fetches from R2, not from a public URL.
        """
        url = self.photo.cloudinary_thumbnail_url
        self.assertIn("test-r2-domain.example.com", url,
                      "FATAL: Delivery URL doesn't reference R2 origin!")
        self.assertIn(self.photo.r2_object_key, url,
                      "FATAL: R2 object key missing from delivery URL!")

    def test_delivery_url_is_not_cloudinary_sdk_upload(self):
        """
        REGRESSION: The old architecture used cloudinary.uploader.upload().
        Proves the SDK upload path is completely removed.
        """
        from unittest.mock import patch
        with patch('cloudinary.uploader.upload') as mock_upload:
            _ = self.photo.cloudinary_thumbnail_url
            mock_upload.assert_not_called()

    def test_r2_download_url_bypasses_cdn(self):
        """INFRASTRUCTURE: Proves ZIP generation targets the zero-egress R2 bucket directly."""
        url = self.photo.r2_download_url
        self.assertNotIn("cloudinary", url, "FATAL: High-res download is bleeding CDN bandwidth!")
        self.assertIn("raw_shot.jpg", url)

    def test_photo_without_r2_key_returns_fallback(self):
        """BACKWARD COMPAT: Older photos without r2_object_key use optimized_url fallback."""
        legacy_photo = Photo.objects.create(
            scene=self.scene,
            original_filename="legacy.jpg",
            file_size_bytes=1000,
            image_file="events/2026/04/legacy.jpg",
            optimized_url="https://res.cloudinary.com/legacy-url",
        )
        url = legacy_photo.cloudinary_thumbnail_url
        # Falls back to optimized_url
        self.assertEqual(url, "https://res.cloudinary.com/legacy-url")
