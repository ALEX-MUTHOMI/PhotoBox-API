from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from core.models import Workspace # Assuming Workspace remains in core
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

        # 1. The database must not contain the raw PIN anywhere
        self.assertNotEqual(self.event._hashed_pin, raw_pin, "FATAL: PIN stored in plain text!")
        self.assertTrue(self.event._hashed_pin.startswith('pbkdf2_') or 'argon2' in self.event._hashed_pin)

        # 2. The verification function must mathematically prove the match
        self.assertTrue(self.event.check_pin("4920"), "FATAL: Correct PIN rejected.")
        self.assertFalse(self.event.check_pin("0000"), "FATAL: Incorrect PIN accepted.")
        self.assertFalse(self.event.check_pin("49201"), "FATAL: PIN length overflow bypass allowed.")

    def test_event_slug_uniqueness(self):
        """INFRASTRUCTURE: Prevents two events from hijacking the same public URL."""
        with self.assertRaises(IntegrityError):
            Event.objects.create(
                workspace=self.workspace,
                title="Another Launch",
                slug="safaricom-launch-2026" # Duplicate slug
            )


class PhotoCloudinaryTests(TestCase):
    """The Hacker's Domain: Attacking the CDN Delivery mechanisms."""

    def setUp(self):
        self.user = User.objects.create_user(email="dev@test.com", password="password123")
        self.workspace = Workspace.objects.create(user=self.user, business_name="Cloud Studios")
        self.event = Event.objects.create(workspace=self.workspace, title="Cloud Event", slug="cloud-event")
        self.scene = Scene.objects.create(event=self.event, title="Keynote", display_order=1)

        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="raw_shot.jpg",
            file_size_bytes=25000000, # 25MB
            image_file="events/2026/04/raw_shot.jpg",
            is_processed=True
        )

    @patch('cloudinary.utils.cloudinary_url')
    def test_cloudinary_url_is_cryptographically_signed(self, mock_cloudinary_url):
        """SECURITY: Proves the CDN URL cannot be tampered with to steal un-watermarked photos."""
        # Mocking the cloudinary SDK response to simulate a signed URL
        mock_cloudinary_url.return_value = (
            "https://res.cloudinary.com/demo/image/upload/s--8xkj3j--/w_800,l_watermark/raw_shot.jpg",
            {}
        )

        url = self.photo.cloudinary_thumbnail_url

        # The URL must contain the Cloudinary signature parameter (s--...--)
        self.assertIn("s--", url, "FATAL: Cloudinary URL is missing cryptographic signature!")
        mock_cloudinary_url.assert_called_once()

        # Verify the exact parameters sent to the SDK request the signature
        call_kwargs = mock_cloudinary_url.call_args[1]
        self.assertTrue(call_kwargs.get('sign_url'), "FATAL: SDK was not instructed to sign the URL!")
        self.assertEqual(call_kwargs.get('width'), 800)
        self.assertEqual(call_kwargs.get('fetch_format'), 'auto')

    def test_r2_download_url_bypasses_cdn(self):
        """INFRASTRUCTURE: Proves ZIP generation targets the zero-egress R2 bucket directly."""
        url = self.photo.r2_download_url
        self.assertNotIn("cloudinary", url, "FATAL: High-res download is bleeding CDN bandwidth!")
        self.assertIn("raw_shot.jpg", url)
