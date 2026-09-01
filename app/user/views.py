"""
Views for the user API.
"""
import logging
import hashlib
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed

from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView

from core.api_errors import dual_key_error
from core.security import scrub_email, scrub_ip
from core.turnstile import verify_turnstile_token
from user.password_reset_serializers import (
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
)
from user.serializers import UserSerializer
from user.throttles import PasswordResetRequestThrottle

logger = logging.getLogger(__name__)

# ==========================================
# 1. THE PUBLIC DOOR (Hardened Onboarding)
# ==========================================
class CreateUserView(generics.CreateAPIView):
    """Create a new user in the system with military-grade Anti-Fraud."""
    serializer_class = UserSerializer

    def verify_turnstile(self, token, ip_address):
        if getattr(settings, 'TESTING', False) and token == 'valid':  # nosec B105 - test-only sentinel.
            return True
        return verify_turnstile_token(token, ip_address)

    def create(self, request, *args, **kwargs):
        # 1. Extract IP (Strips comma-separated spoofed proxy blocks)
        cf_ip = request.META.get('HTTP_CF_CONNECTING_IP', '')
        if getattr(settings, 'TRUST_CLOUDFLARE_CLIENT_IP', False) and cf_ip:
            raw_ip = cf_ip.split(',')[0].strip()
        else:
            raw_ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
        if not raw_ip:
            return dual_key_error('Direct access blocked.', status_code=status.HTTP_403_FORBIDDEN)

        # 2. Redis Velocity Lock (Anti-Botnet)
        salt = settings.IP_HASH_SALT
        ip_hash = hashlib.sha256((raw_ip + salt).encode('utf-8')).hexdigest()
        redis_key = f"reg_lock_{ip_hash}"

        try:
            current_burst_count = cache.incr(redis_key)
            if current_burst_count == 1:
                cache.expire(redis_key, 60) # 1 minute rolling window
        except Exception:
            current_burst_count = 1

        if current_burst_count > 5:
            return dual_key_error('High network volume.', status_code=status.HTTP_429_TOO_MANY_REQUESTS)

        # 3. Validate Serializer
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # 4. Turnstile Bot Check
        token = serializer.validated_data.get('cf_turnstile_response')
        if not token or not self.verify_turnstile(token, raw_ip):
            return dual_key_error('Security challenge failed.', status_code=status.HTTP_403_FORBIDDEN)

        # 5. Execute Creation
        self.perform_create(serializer)

        try:
            from billing.models import RegistrationLog
            RegistrationLog.objects.create(
                email=serializer.validated_data.get('email', ''),
                ip_hash=ip_hash,
                user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:512],
            )
        except Exception:
            logger.exception("Failed to persist registration audit log.")

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
        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            rotated_refresh = response.data.get('refresh')
            if rotated_refresh:
                del response.data['refresh']

                seven_days_in_seconds = 60 * 60 * 24 * 7
                response.set_cookie(
                    key='refresh',
                    value=rotated_refresh,
                    max_age=seven_days_in_seconds,
                    expires=seven_days_in_seconds,
                    secure=True,
                    httponly=True,
                    samesite='Lax'
                )

        return response


# ==========================================
# 4. IDENTITY FEDERATION (Google Auth)
# ==========================================
class GoogleLoginView(SocialLoginView):
    adapter_class = GoogleOAuth2Adapter
    client_class = OAuth2Client
    callback_url = f"{settings.FRONTEND_URL}/api/auth/callback/google/"


class PasswordResetRequestView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [PasswordResetRequestThrottle]

    def post(self, request, *args, **kwargs):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].strip().lower()

        user = get_user_model().objects.filter(email__iexact=email, is_active=True).first()
        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            reset_url = (
                f"{settings.FRONTEND_URL.rstrip('/')}/reset-password"
                f"?uid={uid}&token={token}"
            )
            send_mail(
                subject="Reset your PhotoBox password",
                message=f"Use this secure link to reset your password: {reset_url}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )

        return Response(
            {"detail": "If an account exists for that email, a reset link has been sent."},
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user_id = force_str(urlsafe_base64_decode(serializer.validated_data["uid"]))
            user = get_user_model().objects.get(pk=user_id, is_active=True)
        except Exception:
            return Response(
                {"detail": "Invalid password reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not default_token_generator.check_token(user, serializer.validated_data["token"]):
            return Response(
                {"detail": "Invalid password reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(serializer.validated_data["password"])
        user.save(update_fields=["password"])
        return Response(
            {"detail": "Password has been reset."},
            status=status.HTTP_200_OK,
        )
