"""
Views for the user API.
"""
import logging
import hashlib
import requests
from django.conf import settings
from django.core.cache import cache
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed

from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView

from core.security import scrub_email, scrub_ip
from user.serializers import UserSerializer

logger = logging.getLogger(__name__)

# ==========================================
# 1. THE PUBLIC DOOR (Hardened Onboarding)
# ==========================================
class CreateUserView(generics.CreateAPIView):
    """Create a new user in the system with military-grade Anti-Fraud."""
    serializer_class = UserSerializer

    def verify_turnstile(self, token, ip_address):
        if getattr(settings, 'TESTING', False) and token == 'valid':
            return True
        try:
            res = requests.post('https://challenges.cloudflare.com/turnstile/v0/siteverify', data={
                'secret': getattr(settings, 'TURNSTILE_SECRET_KEY', 'dummy'),
                'response': token, 'remoteip': ip_address
            }, timeout=5)
            return res.json().get('success', False)
        except requests.RequestException:
            return False

    def create(self, request, *args, **kwargs):
        # 1. Extract IP (Strips comma-separated spoofed proxy blocks)
        cf_ip = request.META.get('HTTP_CF_CONNECTING_IP', '')
        raw_ip = cf_ip.split(',')[0].strip() if cf_ip else request.META.get('REMOTE_ADDR', '127.0.0.1')
        if not raw_ip:
            return Response({'error': 'Direct access blocked.'}, status=status.HTTP_403_FORBIDDEN)

        # 2. Redis Velocity Lock (Anti-Botnet)
        salt = getattr(settings, 'IP_HASH_SALT', 'default_salt')
        ip_hash = hashlib.sha256((raw_ip + salt).encode('utf-8')).hexdigest()
        redis_key = f"reg_lock_{ip_hash}"

        try:
            current_burst_count = cache.incr(redis_key)
            if current_burst_count == 1:
                cache.expire(redis_key, 60) # 1 minute rolling window
        except Exception:
            current_burst_count = 1

        if current_burst_count > 5:
            return Response({'error': 'High network volume.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        # 3. Validate Serializer
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 4. Turnstile Bot Check
        token = serializer.validated_data.get('cf_turnstile_response')
        if not token or not self.verify_turnstile(token, raw_ip):
            return Response({'error': 'Security challenge failed.'}, status=status.HTTP_403_FORBIDDEN)

        # 5. Execute Creation
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


# ==========================================
# 2. PROFILE MANAGEMENT (The VIP Section)
# ==========================================
class ManageUserView(generics.RetrieveUpdateAPIView):
    """Manage the authenticated user."""
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


# ==========================================
# 3. THE IDENTITY VAULT (Authentication & XSS Shield)
# ==========================================
class EnterpriseTokenObtainPairView(TokenObtainPairView):
    """Overrides JWT view to inject Threat Logging & HttpOnly cookie (XSS Shield)."""
    def post(self, request, *args, **kwargs):
        ip_address = scrub_ip(request.META.get('REMOTE_ADDR', 'Unknown IP'))
        email_attempt = scrub_email(request.data.get('email', ''))

        try:
            response = super().post(request, *args, **kwargs)
            logger.info("SUCCESSFUL LOGIN: principal=%s ip=%s", email_attempt, ip_address)

            # THE XSS SHIELD
            if response.status_code == 200:
                refresh_token = response.data.get('refresh')
                if refresh_token:
                    del response.data['refresh']

                seven_days_in_seconds = 60 * 60 * 24 * 7
                response.set_cookie(
                    key='refresh',
                    value=refresh_token,
                    max_age=seven_days_in_seconds,
                    expires=seven_days_in_seconds,
                    secure=True,
                    httponly=True,
                    samesite='Lax'
                )
            return response
        except (InvalidToken, AuthenticationFailed) as e:
            logger.warning("FAILED LOGIN ATTEMPT: principal=%s ip=%s", email_attempt, ip_address)
            raise e

class CookieTokenRefreshView(TokenRefreshView):
    """Extracts the refresh token from the HttpOnly cookie for SimpleJWT."""
    def post(self, request, *args, **kwargs):
        refresh_cookie = request.COOKIES.get('refresh')
        if refresh_cookie:
            if hasattr(request.data, '_mutable'):
                request.data._mutable = True
            request.data['refresh'] = refresh_cookie
        return super().post(request, *args, **kwargs)


# ==========================================
# 4. IDENTITY FEDERATION (Google Auth)
# ==========================================
class GoogleLoginView(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    client_class = OAuth2Client
    callback_url = f"{settings.FRONTEND_URL}/api/auth/callback/google/"
