import requests
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import UserRateThrottle
from django.core.cache import cache
from django.conf import settings
from .models import PricingPlan, CheckoutSession
from .serializers import PricingPlanSerializer, CheckoutRequestSerializer

# --- FRAUD DEFENSE: Aggressive Rate Limiting ---
class CheckoutRateThrottle(UserRateThrottle):
    rate = '5/min'
    def get_rate(self):
        return self.rate


class PricingPlanListView(APIView):
    """PUBLIC: Returns available pricing plans for the frontend."""
    permission_classes = [AllowAny]

    def get(self, request):
        # The Graveyard Defense: Only expose active plans
        plans = PricingPlan.objects.filter(is_active=True)
        serializer = PricingPlanSerializer(plans, many=True)
        return Response(serializer.data)


class GenerateCheckoutLinkView(APIView):
    """SECURE: Generates unique Lemon Squeezy URLs with injected custom data."""
    permission_classes = [IsAuthenticated]
    throttle_classes = [CheckoutRateThrottle]

    def post(self, request):
        # 1. STATE EXPLOITATION DEFENSE (Double-Bill Glitch)
        if getattr(request.user, 'is_pro', False):
            return Response({"error": "You already have an active subscription."}, status=status.HTTP_400_BAD_REQUEST)

        # 2. THE BOUNCER (Delegating to the Input Serializer)
        serializer = CheckoutRequestSerializer(data=request.data)
        if not serializer.is_valid():
            # To maintain backward compatibility with our strict strict security tests,
            # if the plan is missing/retired, we return a 404 instead of a standard 400.
            if 'plan_id' in serializer.errors and serializer.errors['plan_id'][0].code == 'does_not_exist':
                return Response({"error": "Invalid or retired plan selected."}, status=status.HTTP_404_NOT_FOUND)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        # Extract the securely validated objects
        plan = serializer.validated_data['plan_id']
        success_url = serializer.validated_data.get('success_url')

        # 3. RACE CONDITION DEFENSE (The Double-Tap Cache Lock)
        lock_key = f"checkout_lock_{request.user.id}"
        if not cache.add(lock_key, "locked", timeout=5):
            return Response({"error": "Request already processing. Please wait."}, status=status.HTTP_409_CONFLICT)

        try:
            # 4. AMATEUR DEVOPS DEFENSE (Environment Sabotage)
            api_key = getattr(settings, 'LEMON_SQUEEZY_API_KEY', None)
            if not api_key:
                return Response({"error": "Payment gateway not configured."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Log the intent in the database (Failsafe for Webhooks)
            session = CheckoutSession.objects.create(user=request.user, plan=plan)

            # 5. THE PAYLOAD VAULT (Identity Anchoring & Redirection)
            headers = {
                'Accept': 'application/vnd.api+json',
                'Content-Type': 'application/vnd.api+json',
                'Authorization': f'Bearer {api_key}'
            }

            payload = {
                "data": {
                    "type": "checkouts",
                    "attributes": {
                        "checkout_data": {
                            "custom": {
                                "user_id": str(request.user.id),
                                "session_token": str(session.session_token)
                            }
                        }
                    },
                    "relationships": {
                        "store": {
                            "data": {"type": "stores", "id": getattr(settings, 'LEMON_SQUEEZY_STORE_ID', '1')}
                        },
                        "variant": {
                            "data": {"type": "variants", "id": str(plan.lemon_squeezy_variant_id)}
                        }
                    }
                }
            }

            # If the frontend provided a validated redirect URL, inject it into the Lemon Squeezy options
            if success_url:
                payload["data"]["attributes"]["checkout_options"] = {
                    "redirect_url": success_url
                }

            # 6. INFRASTRUCTURE FAILURES DEFENSE (Chaos Engineering)
            try:
                ls_response = requests.post(
                    'https://api.lemonsqueezy.com/v1/checkouts',
                    json=payload,
                    headers=headers,
                    timeout=5  # Strict timeout to prevent thread hanging
                )

                if ls_response.status_code >= 500:
                    return Response({"error": "Payment provider is currently degraded."}, status=status.HTTP_502_BAD_GATEWAY)

                ls_response.raise_for_status()

                data = ls_response.json()
                checkout_url = data['data']['attributes']['url']
                return Response({"url": checkout_url}, status=status.HTTP_200_OK)

            except requests.exceptions.Timeout:
                return Response({"error": "Payment gateway timed out."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            except requests.exceptions.RequestException:
                return Response({"error": "Failed to communicate with payment provider."}, status=status.HTTP_502_BAD_GATEWAY)

        finally:
            # SECURITY FIX: Was `pass` — cache lock was NEVER released!
            # User would be permanently locked after first checkout attempt
            # until the 5-second cache TTL expired. Under load this causes
            # cascading 409 CONFLICT responses for legitimate users.
            cache.delete(lock_key)
