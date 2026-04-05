"""
Views for the user API.
"""
import logging
from django.conf import settings # NEW: Required for dynamic frontend routing
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed

from rest_framework_simplejwt.views import TokenRefreshView

# NEW: Federation Imports
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter
from allauth.socialaccount.providers.oauth2.client import OAuth2Client
from dj_rest_auth.registration.views import SocialLoginView

from user.serializers import UserSerializer

# Initialize the enterprise logger
logger = logging.getLogger(__name__)

class CreateUserView(generics.CreateAPIView):
    """Create a new user in the system. (The Public Sign-Up Door)"""
    serializer_class = UserSerializer


class ManageUserView(generics.RetrieveUpdateAPIView):
    """Manage the authenticated user. (The VIP Section)"""
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        """Retrieve and return the strictly authenticated user."""
        return self.request.user


class EnterpriseTokenObtainPairView(TokenObtainPairView):
    """
    SECURITY ENHANCED LOGIN:
    Overrides the default JWT view to inject Active Threat Logging
    and secure the Refresh token inside an HttpOnly cookie (XSS Shield).
    """
    def post(self, request, *args, **kwargs):
        # 1. Capture the incoming IP address for security audits
        ip_address = request.META.get('REMOTE_ADDR', 'Unknown IP')
        email_attempt = request.data.get('email', 'No Email Provided')

        try:
            # 2. Attempt the standard Argon2 verification and Token Generation
            response = super().post(request, *args, **kwargs)

            # 3. If successful, log it for compliance (do NOT log passwords!)
            logger.info(f"SUCCESSFUL LOGIN: User {email_attempt} from IP {ip_address}")

            # 4. THE XSS SHIELD: Intercept the tokens before they reach the frontend
            if response.status_code == 200:
                refresh_token = response.data.get('refresh')

                # THE VAULT: Delete it from the JSON body so frontend JS can NEVER see it
                if refresh_token:
                    del response.data['refresh']

                # THE TRANSPORT: Weld it into the HTTP headers
                seven_days_in_seconds = 60 * 60 * 24 * 7

                response.set_cookie(
                    key='refresh',
                    value=refresh_token,
                    max_age=seven_days_in_seconds,
                    expires=seven_days_in_seconds,
                    secure=True,     # Only send over HTTPS
                    httponly=True,   # BLOCKS JAVASCRIPT (The XSS Shield)
                    samesite='Lax'   # CSRF Protection
                )

            return response

        except (InvalidToken, AuthenticationFailed) as e:
            # 5. ACTIVE THREAT LOGGING: Record the failed attempt to monitor for Brute Force
            logger.warning(f"FAILED LOGIN ATTEMPT: Target {email_attempt} from IP {ip_address}")
            raise e

# ==========================================
# THE XSS SHIELD REFRESHER
# ==========================================
class CookieTokenRefreshView(TokenRefreshView):
    """
    SECURITY: Custom Refresh View for the XSS Shield.
    The default SimpleJWT view expects the refresh token in the JSON body.
    We locked it in an HttpOnly cookie. This view intercepts the request and
    extracts the token from the cookie vault so the library can process it.
    """
    def post(self, request, *args, **kwargs):
        # Extract the 7-day master key from the HTTP headers (the cookie)
        refresh_cookie = request.COOKIES.get('refresh')

        # Inject it into the data payload so the default SimpleJWT engine can read it
        if refresh_cookie:
            # We must make the QueryDict mutable to inject our cookie data
            if hasattr(request.data, '_mutable'):
                request.data._mutable = True
            request.data['refresh'] = refresh_cookie

        return super().post(request, *args, **kwargs)


    
# ==========================================
# IDENTITY FEDERATION (SIDE DOOR)
# ==========================================
class GoogleLoginView(SocialLoginView):
    """
    SECURITY: Google Identity Federation View.
    Handles the OAuth2 callback and enforces the strict ownership rules in settings.py.
    """
    adapter_class = GoogleOAuth2Adapter
    client_class = OAuth2Client
    # Dynamically pull the URL so it doesn't break in production
    callback_url = f"{settings.FRONTEND_URL}/api/auth/callback/google/"
