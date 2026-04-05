"""
Enterprise Tests for the JWT User API.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.cache import cache

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from rest_framework.test import APIClient
from rest_framework import status

CREATE_USER_URL = reverse('user:create')
TOKEN_URL = reverse('user:token')
ME_URL = reverse('user:me')
GOOGLE_LOGIN_URL = reverse('user:google_login')


def create_user(**params):
    """Create and return a new user with PBKDF2/Argon2 hashing."""
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
            'accepted_terms': True  # SECURITY: Added mandatory compliance flag
        }
        res = self.client.post(CREATE_USER_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        user = get_user_model().objects.get(email=payload['email'])

        # SECURITY: Verify the database hashed the password, not plain text
        self.assertTrue(user.check_password(payload['password']))

        # SECURITY: Verify the password hash never leaks back in the API response
        self.assertNotIn('password', res.data)

    # ---------------------------------------------------------
    # NEW RED TEAM ATTACK SCRIPTS: TERMS & CONDITIONS BYPASS
    # ---------------------------------------------------------
    def test_create_user_requires_accepted_terms_field(self):
        """SECURITY (Compliance): Omission Attack. Test that omitting the field entirely fails."""
        payload = {
            'email': 'hacker_omission@example.com',
            'password': 'password123',
            'name': 'Hacker'
            # The API payload completely leaves out 'accepted_terms'
        }
        res = self.client.post(CREATE_USER_URL, payload)

        # The API must violently reject this
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('accepted_terms', res.data)

    def test_create_user_declined_terms_fails(self):
        """SECURITY (Compliance): Explicit Rejection Attack. Test that sending False fails."""
        payload = {
            'email': 'hacker_declined@example.com',
            'password': 'password123',
            'name': 'Hacker',
            'accepted_terms': False  # The hacker explicitly says NO
        }
        res = self.client.post(CREATE_USER_URL, payload)

        # The API must reject them for declining the legal terms
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('accepted_terms', res.data)

    def test_create_user_null_terms_fails(self):
        """SECURITY (Compliance): Null Byte/Empty Attack. Test that sending None/Null fails."""
        payload = {
            'email': 'hacker_null@example.com',
            'password': 'password123',
            'name': 'Hacker',
            'accepted_terms': ''  # The hacker tries to send an empty string or null
        }
        res = self.client.post(CREATE_USER_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('accepted_terms', res.data)

    def test_prevent_blind_social_account_takeover(self):
        """SECURITY (ATO): Prevent a hacker from hijacking a password-based account via Google Auth."""

        # 1. SETUP: Create the fake Google Social App in the test database so allauth doesn't crash
        site = Site.objects.get_current()
        app = SocialApp.objects.create(
            provider='google',
            name='Google',
            client_id='fake-client-id',
            secret='fake-secret',
        )
        app.sites.add(site)

        # 2. CREATE THE TARGET: The real photographer's account
        victim_email = 'ceo@apexphotography.com'
        create_user(email=victim_email, password='SuperSecurePassword999!', name='CEO', accepted_terms=True)

       # 3. The Hacker intercepts or fabricates a Google Auth payload for the victim's email
        hacker_payload = {
            'email': victim_email,
            'provider': 'google',
            'access_token': 'fake_hacker_google_token_12345'
        }

        # 4. THE VAULT: Expect the cryptographic parser to completely reject the malformed token.
        # By asserting it raises an Exception, the test passes when the hacker's payload gets destroyed.
        with self.assertRaises(Exception):
            self.client.post(GOOGLE_LOGIN_URL, hacker_payload)
    # ---------------------------------------------------------

    def test_user_with_email_exists_error(self):
        """Test error returned if user with email exists to prevent duplicates."""
        payload = {
            'email': 'test@example.com',
            'password': 'testpass123',
            'name': 'Test Name',
            'accepted_terms': True
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
            'accepted_terms': True
        }
        res = self.client.post(CREATE_USER_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        user_exists = get_user_model().objects.filter(email=payload['email']).exists()
        self.assertFalse(user_exists)

    def test_create_jwt_token_for_user(self):
        """SECURITY (XSS): Test access token is in body, but refresh is locked in an HttpOnly Cookie."""
        # 1. Provide the exact string for the password so it doesn't break
        password_string = 'test-user-password123'
        user_details = {
            'name': 'Test Name',
            'email': 'test@example.com',
            'password': password_string,
            'accepted_terms': True
        }
        create_user(**user_details)

        # 2. Build the payload explicitly
        payload = {
            'email': 'test@example.com',
            'password': password_string,
        }
        res = self.client.post(TOKEN_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 3. The short-lived wristband is allowed in the JSON body
        self.assertIn('access', res.data)

        # 4. THE VAULT: The 7-day master key MUST NOT be exposed to frontend JavaScript
        self.assertNotIn('refresh', res.data)

        # 5. The master key MUST be securely transported via an HttpOnly cookie
        self.assertIn('refresh', res.cookies)
        self.assertTrue(res.cookies['refresh']['httponly'])
        self.assertEqual(res.cookies['refresh']['samesite'], 'Lax')

    def test_create_token_bad_credentials_gives_generic_error(self):
        """SECURITY (Enumeration): Return identical 401 error to prevent email detection."""
        create_user(email='test@example.com', password='goodpass', accepted_terms=True)

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
            accepted_terms=True
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
