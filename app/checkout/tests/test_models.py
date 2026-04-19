from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from checkout.models import PricingPlan, CheckoutSession
from django.contrib.auth import get_user_model

User = get_user_model()

class CheckoutModelTests(TestCase):
    """Physics, Integrity, and Security tests for the Checkout database schema."""

    def setUp(self):
        self.user = User.objects.create_user(email="client@example.com", password="password123")
        self.plan = PricingPlan.objects.create(
            name="Pro Delivery",
            lemon_squeezy_variant_id="variant_98765",
            price_usd=15.00,
            bandwidth_limit_bytes=100_000_000_000, # 100GB
            gallery_expiry_days=30,
            commission_rate=0.0
        )

    # --- 1. INTEGRITY & BUSINESS LOGIC ---

    def test_checkout_session_defaults_to_pending(self):
        """INTEGRITY: When an intent is logged, it MUST default to PENDING with a valid UUID."""
        session = CheckoutSession.objects.create(user=self.user, plan=self.plan)

        self.assertEqual(session.status, 'PENDING')
        self.assertIsNotNone(session.session_token)
        self.assertEqual(len(str(session.session_token)), 36) # Valid UUID length

    def test_pricing_plan_protection(self):
        """
        BUSINESS LOGIC: If a user has generated a checkout session for a plan,
        an admin CANNOT delete that plan from the database, otherwise it orphans the session.
        """
        # 1. Create a session tied to the plan
        CheckoutSession.objects.create(user=self.user, plan=self.plan)

        # 2. Try to delete the plan. The DB must block it to prevent data corruption.
        with self.assertRaises(ProtectedError):
            self.plan.delete()

    # --- 2. HACKER & DATA VALIDATION DEFENSES ---

    def test_negative_bandwidth_rejected(self):
        """HACKER: Tries to inject a negative storage quota into the database."""
        bad_plan = PricingPlan(
            name="Corrupted Plan",
            lemon_squeezy_variant_id="variant_evil_1",
            price_usd=10.00,
            bandwidth_limit_bytes=-500, # IMPOSSIBLE DATA
            gallery_expiry_days=30
        )

        # We expect Django to throw a ValidationError because of our MinValueValidator
        with self.assertRaises(ValidationError):
            bad_plan.full_clean()

    def test_negative_pricing_and_impossible_commission_rejected(self):
        """HACKER: Tries to set a negative price or a commission over 100%."""
        bad_plan = PricingPlan(
            name="Robin Hood Plan",
            lemon_squeezy_variant_id="variant_evil_2",
            price_usd=-10.00,  # NEGATIVE PRICE
            bandwidth_limit_bytes=100_000,
            gallery_expiry_days=30,
            commission_rate=150.00 # IMPOSSIBLE COMMISSION
        )

        with self.assertRaises(ValidationError):
            bad_plan.full_clean()

    def test_zero_day_gallery_expiry_rejected(self):
        """HACKER: Tries to set a gallery to expire instantly (0 days) or negatively."""
        bad_plan = PricingPlan(
            name="Blink Plan",
            lemon_squeezy_variant_id="variant_evil_3",
            price_usd=10.00,
            bandwidth_limit_bytes=100_000,
            gallery_expiry_days=0, # IMPOSSIBLE EXPIRY
        )

        with self.assertRaises(ValidationError):
            bad_plan.full_clean()
