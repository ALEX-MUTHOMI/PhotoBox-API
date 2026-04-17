from unittest.mock import patch
import requests
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from django.test import override_settings
from checkout.models import PricingPlan

User = get_user_model()


@override_settings(LEMON_SQUEEZY_API_KEY="dummy_test_key_12345", LEMON_SQUEEZY_STORE_ID="1")
class GatewayIntegrationTests(APITestCase):
    """Chaos and Payload Integration tests for the Lemon Squeezy Gateway."""

    def setUp(self):
        self.user = User.objects.create_user(email="paying_client@example.com", password="password123")

        # 1. SEED THE DATABASE: Gives the Bouncer a valid plan to find, preventing 404s.
        self.plan = PricingPlan.objects.create(
            name="Test Gateway Plan",
            lemon_squeezy_variant_id="var_gateway_123",
            price_usd=15.00,
            bandwidth_limit_bytes=1073741824,
            gallery_expiry_days=30,
            is_active=True
        )

        self.generate_url = '/api/checkout/generate/'

    # --- 1. THE PAYLOAD VAULT (Happy Paths) ---

    @patch('requests.post')
    def test_checkout_generation_injects_custom_data(self, mock_post):
        """THE VAULT: Proves we inject user_id and session_token into the payload."""
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {
            "data": {"attributes": {"url": "https://photobox.lemonsqueezy.com/checkout/secure_123"}}
        }

        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.generate_url, {"plan_id": self.plan.id})

        self.assertTrue(mock_post.called, "The view never attempted to contact Lemon Squeezy.")

        sent_json = mock_post.call_args[1].get('json', {})
        custom_data = sent_json.get('data', {}).get('attributes', {}).get('checkout_data', {}).get('custom', {})

        self.assertIn('user_id', custom_data, "FATAL: user_id missing from outbound payload!")
        self.assertEqual(custom_data['user_id'], str(self.user.id), "FATAL: Wrong user_id sent!")
        self.assertIn('session_token', custom_data, "FATAL: session_token missing from outbound payload!")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    @override_settings(ALLOWED_HOSTS=['*'])
    @patch('checkout.serializers.CheckoutRequestSerializer.validate_success_url')
    @patch('requests.post')
    def test_checkout_generation_injects_redirect_url(self, mock_post, mock_url_validator):
        """THE VAULT: Proves a validated success_url is successfully forwarded to Lemon Squeezy."""

        # 1. MOCK THE BOUNCER: Force the serializer to accept any URL for this test.
        valid_url = "https://photobox.com/dashboard/success"
        mock_url_validator.return_value = valid_url

        # 2. MOCK THE API:
        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {
            "data": {"attributes": {"url": "https://photobox.lemonsqueezy.com/checkout/secure_123"}}
        }

        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.generate_url, {
            "plan_id": self.plan.id,
            "success_url": valid_url
        })

        # 3. VERIFY THE PAYLOAD: Did the URL make it into the JSON?
        sent_json = mock_post.call_args[1].get('json', {})
        checkout_options = sent_json.get('data', {}).get('attributes', {}).get('checkout_options', {})

        self.assertIn('redirect_url', checkout_options, "FATAL: redirect_url missing from LS payload!")
        self.assertEqual(checkout_options['redirect_url'], valid_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)



    # --- 2. CHAOS ENGINEERING: INFRASTRUCTURE FAILURES ---

    @patch('requests.post')
    def test_gateway_timeout_fails_gracefully(self, mock_post):
        """CHAOS: Lemon Squeezy's servers are completely unresponsive."""
        mock_post.side_effect = requests.exceptions.Timeout("Connection timed out")

        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.generate_url, {"plan_id": self.plan.id})

        self.assertEqual(
            response.status_code,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "FATAL: Server crashed instead of handling the API timeout gracefully!"
        )

    @patch('requests.post')
    def test_gateway_500_error_handled(self, mock_post):
        """CHAOS: Lemon Squeezy's servers return a 500 Internal Server Error."""
        mock_post.return_value.status_code = 500
        mock_post.return_value.text = "Internal Server Error"

        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.generate_url, {"plan_id": self.plan.id})

        self.assertEqual(
            response.status_code,
            status.HTTP_502_BAD_GATEWAY,
            "FATAL: We did not catch the upstream provider's 500 error!"
        )

    # --- 3. AMATEUR DEVOPS DEFENSE ---

    @override_settings(LEMON_SQUEEZY_API_KEY=None)
    @patch('requests.post')
    def test_missing_api_key_aborts_request(self, mock_post):
        """SABOTAGE: A developer deploys to production but forgets the environment variables."""
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.generate_url, {"plan_id": self.plan.id})

        self.assertFalse(
            mock_post.called,
            "FATAL: The server attempted to contact the payment gateway without an API key!"
        )
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
