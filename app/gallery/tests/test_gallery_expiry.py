"""A2: Event.expires_at is stamped on first publish and drives the expiry sweep."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from unittest.mock import patch

from billing.models import Subscription
from checkout.models import CheckoutSession, PricingPlan
from core.models import Workspace
from gallery.models import Event, Photo, Scene
from gallery.retention import expire_due_galleries

User = get_user_model()


class GalleryExpiryStampTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="expiry@example.com",
            password="StrongPassword123!",
            name="Expiry Photographer",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Expiry Studio",
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="TTL Wedding",
            slug="ttl-wedding",
            client_email="client@example.com",
        )
        self.detail_url = reverse("gallery:event-detail", kwargs={"pk": self.event.id})
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}"
        )

    def _publish(self, extra=None):
        payload = {"is_published": True}
        if extra:
            payload.update(extra)
        with patch("gallery.notifications.send_gallery_ready_email.delay"):
            return self.client.patch(self.detail_url, payload, format="json")

    def test_free_publish_stamps_thirty_days(self):
        before = timezone.now()
        res = self._publish()
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.expires_at)
        expected = before + timedelta(days=30)
        self.assertAlmostEqual(
            self.event.expires_at.timestamp(),
            expected.timestamp(),
            delta=5,
        )

    def test_pro_without_checkout_stamps_365_days(self):
        subscription = Subscription.objects.get(user=self.user)
        subscription.is_pro = True
        subscription.save(update_fields=["is_pro"])

        before = timezone.now()
        res = self._publish()
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.event.refresh_from_db()
        expected = before + timedelta(days=365)
        self.assertAlmostEqual(
            self.event.expires_at.timestamp(),
            expected.timestamp(),
            delta=5,
        )

    def test_completed_checkout_plan_days_win(self):
        plan = PricingPlan.objects.create(
            name="Short",
            lemon_squeezy_variant_id="var-short",
            price_usd="9.00",
            bandwidth_limit_bytes=1024,
            gallery_expiry_days=14,
        )
        CheckoutSession.objects.create(
            user=self.user,
            plan=plan,
            status="COMPLETED",
        )

        before = timezone.now()
        res = self._publish()
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.event.refresh_from_db()
        expected = before + timedelta(days=14)
        self.assertAlmostEqual(
            self.event.expires_at.timestamp(),
            expected.timestamp(),
            delta=5,
        )

    def test_republish_does_not_move_existing_expires_at(self):
        original = timezone.now() + timedelta(days=11)
        self.event.expires_at = original
        self.event.is_published = True
        self.event.save(update_fields=["expires_at", "is_published"])

        self.event.is_published = False
        self.event.save(update_fields=["is_published"])

        res = self._publish()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.event.refresh_from_db()
        self.assertEqual(self.event.expires_at, original)

    def test_patch_expires_at_is_ignored(self):
        attacker_expiry = timezone.now() + timedelta(days=9999)
        res = self._publish(extra={"expires_at": attacker_expiry.isoformat()})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.expires_at)
        self.assertLess(
            self.event.expires_at,
            timezone.now() + timedelta(days=40),
        )

    @override_settings(GALLERY_TTL_DAYS={"FREE": 0, "PRO": 365, "ENTERPRISE": 0})
    def test_unlimited_ttl_leaves_expires_at_null_and_sweep_is_clean(self):
        res = self._publish()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.event.refresh_from_db()
        self.assertIsNone(self.event.expires_at)

        result = expire_due_galleries()
        self.assertEqual(result["status"], "clean")
        self.assertEqual(result["galleries_expired"], 0)

    def test_stamped_gallery_expires_photos_when_due(self):
        scene = Scene.objects.create(event=self.event, title="Ceremony")
        photo = Photo.objects.create(
            scene=scene,
            original_filename="hero.jpg",
            file_size_bytes=100,
            status="READY",
            is_processed=True,
            r2_object_key="tenant/expiry/hero.jpg",
        )

        res = self._publish()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.event.refresh_from_db()
        self.assertIsNotNone(self.event.expires_at)

        Event.objects.filter(pk=self.event.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        result = expire_due_galleries()
        self.assertEqual(result["galleries_expired"], 1)
        photo.refresh_from_db()
        self.assertEqual(photo.status, "EXPIRED")
