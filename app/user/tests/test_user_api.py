"""
Enterprise Tests for the JWT User API .
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.cache import cache

from rest_framework.test import APIClient
from rest_framework import status

CREATE_USER_URL = reverse('user:create')
TOKEN_URL = reverse('user:token')
ME_URL = reverse('user:me')


def create_user(**params):
    """Create and return a new user with PBKDF2 hashing."""
    return get_user_model().objects.create_user(**params)


# ==========================================
# 1. PUBLIC API TESTS (The Front Door)
# ==========================================
class PublicUserApiTests(TestCase):
    """Test the public features of the user API (Registration, Login, Throttling)."""

    def setUp(self):
        self.client = APIClient()

    def tearDown(self):
        """SECURITY TEST RESET: Clear the throttle cache after each test so IP bans don't bleed."""
        cache.clear()

    def test_create_user_success(self):
        """Test creating a user is successful and secure."""
        payload = {
            'email': 'test@example.com',
            'password': 'testpass123',
            'name': 'Test Name',
        }
        res = self.client.post(CREATE_USER_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(email=payload['email'])

        # SECURITY: Verify the database hashed the password, not plain text
        self.assertTrue(user.check_password(payload['password']))

        # SECURITY: Verify the password hash never leaks back in the API response
        self.assertNotIn('password', res.data)

    def test_user_with_email_exists_error(self):
        """Test error returned if user with email exists to prevent duplicates."""
        payload = {
            'email': 'test@example.com',
            'password': 'testpass123',
            'name': 'Test Name',
        }
        create_user(**payload)
        res = self.client.post(CREATE_USER_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_too_short_error(self):
        """SECURITY: Test an error is returned if password is less than 5 chars."""
        payload = {
            'email': 'test@example.com',
            'password': 'pw',  # Too short
            'name': 'Test name',
        }
        res = self.client.post(CREATE_USER_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        user_exists = get_user_model().objects.filter(email=payload['email']).exists()
        self.assertFalse(user_exists)

    def test_create_jwt_token_for_user(self):
        """Test generating JWT access and refresh tokens for valid credentials."""
        user_details = {
            'name': 'Test Name',
            'email': 'test@example.com',
            'password': 'test-user-password123',
        }
        create_user(**user_details)

        payload = {
            'email': user_details['email'],
            'password': user_details['password'],
        }
        res = self.client.post(TOKEN_URL, payload)

        # JWT STANDARD: We expect both an 'access' (Wristband) and 'refresh' (ID Card)
        self.assertIn('access', res.data)
        self.assertIn('refresh', res.data)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_create_token_bad_credentials_gives_generic_error(self):
        """SECURITY (Enumeration): Return identical 401 error to prevent email detection."""
        create_user(email='test@example.com', password='goodpass')

        # Hacker tries guessing the password for a known email
        payload = {'email': 'test@example.com', 'password': 'badpass'}
        res = self.client.post(TOKEN_URL, payload)

        self.assertNotIn('access', res.data)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn('detail', res.data)

    def test_brute_force_throttling_protection(self):
        """SECURITY (Rate Limiting): Ensure a bot gets blocked after too many requests."""
        # Note: This will fail until we implement DRF AnonRateThrottle in settings
        payload = {'email': 'test@example.com', 'password': 'wrongpassword'}

        # Simulate a hacker firing 10 rapid requests
        for _ in range(10):
            res = self.client.post(TOKEN_URL, payload)

        # The final requests should be violently rejected with 429 Too Many Requests
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


# ==========================================
# 2. PRIVATE API TESTS (The VIP Section)
# ==========================================
class PrivateUserApiTests(TestCase):
    """Test API requests that require JWT Authorization."""

    def setUp(self):
        self.user = create_user(
            email='test@example.com',
            password='testpass123',
            name='Test Name',
        )
        self.client = APIClient()
        # This simulates the user attaching their JWT "wristband" to the request header
        self.client.force_authenticate(user=self.user)

    def test_retrieve_profile_success(self):
        """Test retrieving profile for logged in user."""
        res = self.client.get(ME_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['name'], self.user.name)
        self.assertEqual(res.data['email'], self.user.email)

    def test_post_me_not_allowed(self):
        """SECURITY: Ensure hackers cannot completely overwrite a profile via POST."""
        res = self.client.post(ME_URL, {})

        # The API should only allow PATCH (partial update) or PUT, not POST
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_update_profile_without_old_password_fails(self):
        """SECURITY: A hacker at an unlocked computer cannot change the password."""
        # Note: This will fail until we update our Serializer to require old_password
        payload = {'password': 'hackerpassword123'}

        res = self.client.patch(ME_URL, payload)

        # The API should reject this because the user didn't prove who they are
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('old_password', res.data)

    def test_update_profile_with_valid_old_password_succeeds(self):
        """SECURITY: A legitimate user successfully updates their password."""
        payload = {
            'old_password': 'testpass123',
            'password': 'newsecurepassword999'
        }

        res = self.client.patch(ME_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newsecurepassword999'))

    def test_billing_fields_are_read_only(self):
        """SECURITY (Mass Assignment): Prevent hackers from upgrading their subscription."""
        payload = {'subscription_tier': 'PRO', 'storage_limit_gb': 1000}

        res = self.client.patch(ME_URL, payload)

        self.user.refresh_from_db()
        # Ensure the database strictly ignored the hacker's attempt to upgrade
        self.assertEqual(self.user.subscription_tier, 'FREE')
        self.assertEqual(self.user.storage_limit_gb, 5)
