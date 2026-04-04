"""
Views for the user API.
"""
import logging
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.exceptions import InvalidToken, AuthenticationFailed

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
    Overrides the default JWT view to inject Active Threat Logging.
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
            return response

        except (InvalidToken, AuthenticationFailed) as e:
            # 4. ACTIVE THREAT LOGGING: Record the failed attempt to monitor for Brute Force
            logger.warning(f"FAILED LOGIN ATTEMPT: Target {email_attempt} from IP {ip_address}")
            raise e
