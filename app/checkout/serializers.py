from rest_framework import serializers
from urllib.parse import urlparse
from django.conf import settings
from .models import PricingPlan

# ==========================================
# OUTPUT SERIALIZER (Sending data to React)
# ==========================================
class PricingPlanSerializer(serializers.ModelSerializer):
    """
    Formats the pricing data for the public frontend pricing table.
    """
    storage_limit_gb = serializers.SerializerMethodField()

    class Meta:
        model = PricingPlan
        fields = [
            'id',
            'name',
            'price_usd',
            'bandwidth_limit_bytes',
            'storage_limit_gb',
            'gallery_expiry_days',
            'features'
        ]
        # DEFENSE IN DEPTH: Force all fields to read-only
        read_only_fields = fields

    def get_storage_limit_gb(self, obj):
        """Safely converts bytes to GB, keeping 1 decimal place for plans under 1GB."""
        gb_value = obj.bandwidth_limit_bytes / (1024 * 1024 * 1024)
        # If it's a clean whole number (e.g., 10.0), return 10. Otherwise, return 0.5.
        return int(gb_value) if gb_value.is_integer() else round(gb_value, 2)


# ==========================================
# INPUT SERIALIZER (Validating incoming requests)
# ==========================================
class CheckoutRequestSerializer(serializers.Serializer):
    """
    Acts as the strict bouncer for incoming checkout requests.
    """
    plan_id = serializers.PrimaryKeyRelatedField(
        queryset=PricingPlan.objects.filter(is_active=True),
        error_messages={
            'does_not_exist': 'This plan is invalid, retired, or no longer available.',
            'incorrect_type': 'Invalid data format. Expected an integer.'
        }
    )

    # Optional: Allow frontend to pass where to redirect after payment
    success_url = serializers.URLField(required=False, write_only=True)

    def validate_success_url(self, value):
        """
        SECURITY: Open Redirect Defense.
        Only allow redirects back to our own domain or localhost.
        """
        if not value:
            return value

        parsed_url = urlparse(value)
        # In production, settings.ALLOWED_HOSTS typically contains your domains
        # (e.g., ['api.photobox.com', 'photobox.com', 'localhost'])
        allowed_domains = getattr(settings, 'ALLOWED_HOSTS', [])

        # Localhost fallback for development
        if parsed_url.hostname not in allowed_domains and parsed_url.hostname not in ['localhost', '127.0.0.1']:
            raise serializers.ValidationError("Untrusted redirect URL provided. Security violation logged.")

        return value
