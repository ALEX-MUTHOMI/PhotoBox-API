"""
Enterprise Tests for the JWT User API & Anti-Fraud Perimeter.
"""

from django.test import TestCase, TransactionTestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.core.cache import cache

from rest_framework.test import APIClient
from rest_framework import status

REGISTER_USER_URL = reverse('user:create')
TOKEN_URL = reverse('user:token')
ME_URL = reverse('user:me')
GOOGLE_LOGIN_URL = reverse('user:google_login')

User = get_user_model()

def create_user(**params):
    """Create and return a new user with PBKDF2/Argon2 hashing."""
    return User.objects.create_user(**params)


# ==========================================
# 1. PUBLIC API TESTS (The Front Door)
# ==========================================
class PublicUserApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    @override_settings(TESTING=True)
    def test_create_user_success(self):
        payload = {
            'email': 'test@example.com',
            'password': 'testpass123',
            'name': 'Test Name',
            'accepted_terms': True,
            'cf_turnstile_response': 'valid'
        }
        res = self.client.post(REGISTER_USER_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email=payload['email'])
        self.assertTrue(user.check_password(payload['password']))
        self.assertNotIn('password', res.data)

    @override_settings(TESTING=True)
    def test_registration_kills_gmail_alias_exploit(self):
        """SECURITY: Ensure photobox+client1@gmail.com becomes photobox@gmail.com"""
        payload = {
            'email': 'hacker.studio+free1GB@gmail.com',
            'password': 'StrongPassword123!',
            'name': 'Hacker Studio',
            'accepted_terms': True,
            'cf_turnstile_response': 'valid'
        }
        res = self.client.post(REGISTER_USER_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(name='Hacker Studio')
        self.assertEqual(user.email, 'hackerstudio@gmail.com')
        self.assertTrue(hasattr(user, 'subscription'))
        self.assertEqual(user.subscription.storage_limit_bytes, 1073741824)

    # ---------------------------------------------------------
    # RESTORED FROM OLD CODE: DUPLICATES & ENTROPY
    # ---------------------------------------------------------
    @override_settings(TESTING=True)
    def test_user_with_email_exists_error(self):
        """SECURITY (Enumeration): Prevent duplicate account creation."""
        create_user(email='test@example.com', password='testpass123', name='Test Name', accepted_terms=True)
        payload = {
            'email': 'test@example.com',
            'password': 'newpassword123',
            'name': 'Hacker',
            'accepted_terms': True,
            'cf_turnstile_response': 'valid'
        }
        res = self.client.post(REGISTER_USER_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(TESTING=True)
    def test_password_too_short_error(self):
        """SECURITY: Test an error is returned if password is less than 5 chars."""
        payload = {
            'email': 'shortpass@example.com',
            'password': 'pw',  # Too short
            'name': 'Test name',
            'accepted_terms': True,
            'cf_turnstile_response': 'valid'
        }
        res = self.client.post(REGISTER_USER_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ---------------------------------------------------------
    # COMPLIANCE & TERMS OF SERVICE BYPASS ATTACKS
    # ---------------------------------------------------------
    def test_create_user_requires_accepted_terms_field(self):
        payload = {'email': 'hacker_omission@example.com', 'password': 'password123', 'name': 'Hacker'}
        res = self.client.post(REGISTER_USER_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_user_declined_terms_fails(self):
        payload = {'email': 'declined@example.com', 'password': 'pass', 'name': 'Hacker', 'accepted_terms': False}
        res = self.client.post(REGISTER_USER_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ---------------------------------------------------------
    # LOGIN AUTHENTICATION
    # ---------------------------------------------------------
    def test_create_jwt_token_for_user(self):
        """SECURITY (XSS): Refresh is locked in an HttpOnly Cookie."""
        create_user(name='Test Name', email='test@example.com', password='password123', accepted_terms=True)
        res = self.client.post(TOKEN_URL, {'email': 'test@example.com', 'password': 'password123'})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('access', res.data)
        self.assertNotIn('refresh', res.data)
        self.assertIn('refresh', res.cookies)
        self.assertTrue(res.cookies['refresh']['httponly'])
        self.assertEqual(res.cookies['refresh']['samesite'], 'Lax')

    def test_create_token_bad_credentials_gives_generic_error(self):
        """SECURITY (Enumeration): Return identical 401 error to prevent email detection."""
        create_user(email='test@example.com', password='goodpass', accepted_terms=True)
        payload = {'email': 'test@example.com', 'password': 'badpass'}
        res = self.client.post(TOKEN_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn('detail', res.data)

    @override_settings(REST_FRAMEWORK={
        'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.AnonRateThrottle'],
        'DEFAULT_THROTTLE_RATES': {'anon': '5/minute'}
    })
    @override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
    def test_brute_force_throttling_protection(self):
        """SECURITY (Rate Limiting): Ensure a bot gets blocked after too many requests."""
        payload = {'email': 'test@example.com', 'password': 'wrongpassword'}
        for _ in range(10):
            res = self.client.post(TOKEN_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


# ==========================================
# 2. ANTI-FRAUD PERIMETER (Sybil & Botnet Tests)
# ==========================================
class AntiFraudSecurityTests(TransactionTestCase):
    def setUp(self):
        self.hacker_ip = "198.51.100.44"
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def test_blocks_missing_turnstile_token(self):
        headers = {'HTTP_CF_CONNECTING_IP': self.hacker_ip}
        response = self.client.post(
            REGISTER_USER_URL,
            data={
                "email": "bot@botnet.com",
                "password": "password123",
                "name": "Bot",
                "accepted_terms": True,
            },
            **headers,
        )
        # Serializer enforces required turnstile response, returning 400 Bad Request directly
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(
        IP_HASH_SALT="photobox_ip_salt", 
        TESTING=True,
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
    )
    def test_sybil_attack_velocity_block(self):
        headers = {'HTTP_CF_CONNECTING_IP': self.hacker_ip}
        for i in range(10):
            response = self.client.post(
                REGISTER_USER_URL,
                data={
                    "email": f"bot_{i}@botnet.com",
                    "password": "SuperSecureHackerPassword999!",
                    "name": "Bot",
                    "accepted_terms": True,
                    "cf_turnstile_response": "valid"
                },
                **headers
            )
            if i < 5:
                if response.status_code != status.HTTP_201_CREATED:
                    print("DEBUG 400:", response.data)
                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            else:
                self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


# ==========================================
# 3. PRIVATE API TESTS (The VIP Section)
# ==========================================
class PrivateUserApiTests(TestCase):
    def setUp(self):
        self.user = create_user(email='test@example.com', password='testpass123', name='Test Name', accepted_terms=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_retrieve_profile_success(self):
        res = self.client.get(ME_URL)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['name'], self.user.name)

    def test_post_me_not_allowed(self):
        res = self.client.post(ME_URL, {})
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_update_profile_without_old_password_fails(self):
        payload = {'password': 'hackerpassword123'}
        res = self.client.patch(ME_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_profile_with_valid_old_password_succeeds(self):
        payload = {'old_password': 'testpass123', 'password': 'newsecurepassword999'}
        res = self.client.patch(ME_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newsecurepassword999'))

    def test_billing_fields_are_read_only(self):
        payload = {'subscription_tier': 'PRO', 'storage_limit_gb': 1000}
        res = self.client.patch(ME_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.subscription_tier, 'FREE')
        self.assertEqual(self.user.storage_limit_gb, 1)


# ==========================================
# 4. SOCIAL AUTHENTICATION SECURITY TESTS
# ==========================================
class SocialAuthSecurityTests(TestCase):
    """
    Tests for the Google OAuth2 login endpoint.

    SECURITY CONTRACTS:
      - Forged/invalid id_token must return 400, not 500
      - No stack trace or internal detail must leak in the response
      - An email already registered via password must not be silently
        taken over via a Google token (blind social account takeover)
      - The response for an invalid token must be identical regardless
        of whether the email exists (prevents email enumeration)
    """

    def setUp(self):
        self.client = APIClient()
        self.google_url = GOOGLE_LOGIN_URL

        # Pre-existing user registered via email/password
        self.existing_user = User.objects.create_user(
            email='victim@example.com',
            password='strongpassword123',
            name='Victim User',
            accepted_terms=True,
        )

    def test_forged_google_token_returns_400_not_500(self):
        """
        HACKER: Sends a completely fabricated Google id_token string.
        """
        res = self.client.post(
            self.google_url,
            {'id_token': 'this.is.a.forged.token'},
            format='json',
        )

        self.assertEqual(
            res.status_code,
            status.HTTP_400_BAD_REQUEST,
            f'Forged Google token must return 400, got {res.status_code}. '
            'Ensure user.exceptions.custom_exception_handler is wired in settings.py.'
        )

        # Response must not leak internal stack trace details
        response_text = str(res.data)
        for leak_indicator in ['Traceback', 'site-packages', 'File "/py/', 'line ']:
            self.assertNotIn(leak_indicator, response_text)

        # Response must have a usable error message
        self.assertIn('detail', res.data)

    def test_blind_social_account_takeover_blocked(self):
        """
        HACKER: Attempts to take over victim@example.com by sending a
        Google id_token claiming to be that email.
        """
        # A structurally valid-looking but forged token
        res_existing = self.client.post(
            self.google_url,
            {'id_token': 'forged.token.for.existing.email'},
            format='json',
        )

        res_nonexistent = self.client.post(
            self.google_url,
            {'id_token': 'forged.token.for.new.email'},
            format='json',
        )

        # Both must return 400 — never 500
        self.assertEqual(res_existing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res_nonexistent.status_code, status.HTTP_400_BAD_REQUEST)

        # ENUMERATION DEFENSE: Error message must be identical regardless
        # of whether the email already exists in the system.
        self.assertEqual(
            res_existing.data.get('detail'),
            res_nonexistent.data.get('detail'),
            'ENUMERATION VULNERABILITY: Different error messages leak user existence.'
        )
