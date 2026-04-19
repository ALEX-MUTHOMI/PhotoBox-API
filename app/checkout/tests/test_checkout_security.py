from unittest.mock import patch
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from checkout.models import PricingPlan
from checkout.views import CheckoutRateThrottle, GenerateCheckoutLinkView

User = get_user_model()

class CheckoutAPISecurityTests(APITestCase):
    """Production-Grade Penetration tests for the Checkout API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(email="hacker@example.com", password="password123")

        # 1. SEED THE DATABASE: Give the Bouncer a valid, active plan to find.
        self.plan = PricingPlan.objects.create(
            name="Test Pro Plan",
            lemon_squeezy_variant_id="var_12345",
            price_usd=10.00,
            bandwidth_limit_bytes=1073741824,
            gallery_expiry_days=30,
            is_active=True
        )

        self.plans_url = '/api/checkout/plans/'
        self.generate_url = '/api/checkout/generate/'
        cache.clear()

    # --- 1. VISIBILITY & AUTHENTICATION DEFENSES ---

    def test_unauthenticated_users_can_read_pricing(self):
        """PUBLIC SNEAK: Anyone should be able to see the price list for the frontend."""
        response = self.client.get(self.plans_url)
        self.assertNotEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_users_blocked_from_generation(self):
        """HACKER: Tries to generate a Lemon Squeezy link without logging in."""
        response = self.client.post(self.generate_url, {"plan_id": self.plan.id})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_users_allowed_into_generation(self):
        """AUTHORIZED: Logged in user attempts to generate a link."""
        self.client.force_authenticate(user=self.user)
        # Mocking requests.post isn't here, so it may return 500 if it actually hits LS,
        # but as long as it's not 401 Unauthorized, the auth perimeter works.
        response = self.client.post(self.generate_url, {"plan_id": self.plan.id})
        self.assertNotEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --- 2. NETWORK & CONCURRENCY DEFENSES ---

    @patch.object(GenerateCheckoutLinkView, 'throttle_classes', [CheckoutRateThrottle])
    def test_brute_force_checkout_generation_blocked(self):
        """FRAUD DEFENSE: Hacker writes a script to hammer the generate endpoint.
        Re-enables the per-view throttle (disabled globally during tests) for this test only.
        """
        self.client.force_authenticate(user=self.user)

        responses = []
        for _ in range(10):
            responses.append(self.client.post(self.generate_url, {"plan_id": self.plan.id}))

        status_codes = [resp.status_code for resp in responses]
        self.assertIn(
            status.HTTP_429_TOO_MANY_REQUESTS,
            status_codes,
            "FATAL: Endpoint is vulnerable to brute-force DoS attacks!"
        )

    def test_idempotency_double_tap_defense(self):
        """ENGINEERING REALITY: User double-taps the upgrade button while a request is in-flight.
        Simulates the concurrent second request arriving while the first holds the lock.
        """
        self.client.force_authenticate(user=self.user)

        # Pre-seed the cache lock exactly as the view does — simulating a concurrent in-flight request
        lock_key = f"checkout_lock_{self.user.id}"
        cache.add(lock_key, "locked", timeout=30)  # Hold the lock

        try:
            # This request arrives while the lock is held by the "first" request
            resp = self.client.post(self.generate_url, {"plan_id": self.plan.id})
            self.assertEqual(
                resp.status_code,
                status.HTTP_409_CONFLICT,
                "FATAL: Vulnerable to Double-Tap race conditions!"
            )
        finally:
            cache.delete(lock_key)

    # --- 3. BUSINESS LOGIC & INPUT DEFENSES ---

    def test_malicious_plan_id_injection_blocked(self):
        """HACKER: Sends SQL injection strings, negative numbers, or non-existent plan IDs."""
        self.client.force_authenticate(user=self.user)

        # Attempt 1: Non-existent Plan
        resp_phantom = self.client.post(self.generate_url, {"plan_id": 99999})
        self.assertEqual(resp_phantom.status_code, status.HTTP_404_NOT_FOUND)

        # Attempt 2: Malicious String
        resp_string = self.client.post(self.generate_url, {"plan_id": "DROP TABLE users;"})
        self.assertEqual(resp_string.status_code, status.HTTP_400_BAD_REQUEST)

    def test_active_subscribers_blocked_from_duplicate_checkouts(self):
        """FRAUD DEFENSE: User already has an active Pro plan."""
        self.user.subscription.is_pro = True
        self.user.subscription.save(update_fields=['is_pro'])

        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.generate_url, {"plan_id": self.plan.id})

        self.assertIn(
            response.status_code,
            [status.HTTP_403_FORBIDDEN, status.HTTP_400_BAD_REQUEST],
            "FATAL: The system allows active users to double-subscribe!"
        )

    def test_open_redirect_phishing_defense(self):
        """
        HACKER: Injects a malicious success_url to hijack the checkout flow.
        Verifies the Serializer catches and destroys unapproved domains.
        """
        self.client.force_authenticate(user=self.user)

        payload = {
            "plan_id": self.plan.id,
            "success_url": "https://evil-phishing-site.com/steal-data"
        }

        response = self.client.post(self.generate_url, payload)

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
            "FATAL: The API is vulnerable to Open Redirect attacks!"
        )
        self.assertIn("success_url", response.data)
