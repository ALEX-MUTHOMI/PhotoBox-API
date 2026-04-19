"""
Serializers for the Billing & Quota API.
"""
from rest_framework import serializers
from .models import Subscription

# Notice: SecureRegistrationSerializer has been entirely deleted.
# It lives permanently in the User app now.

class SubscriptionSerializer(serializers.ModelSerializer):
    """
    OUTBOUND: The Diplomat.
    Calculates the exact storage math so the React frontend stays lightweight.
    """
    percentage_used = serializers.SerializerMethodField()
    human_used = serializers.SerializerMethodField()
    human_limit = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'is_pro',
            'storage_limit_bytes',
            'storage_used_bytes',
            'percentage_used',
            'human_used',
            'human_limit'
        ]
        # THE VAULT: Users cannot PUT/PATCH these fields to upgrade themselves.
        read_only_fields = fields

    def get_percentage_used(self, obj):
        if obj.storage_limit_bytes == 0:
            return 100
        # Round to 2 decimal places for clean UI rendering
        percentage = (obj.storage_used_bytes / obj.storage_limit_bytes) * 100
        return round(percentage, 2)

    def _format_bytes(self, size_in_bytes):
        """Engineers handle the math, Frontend paints the picture."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_in_bytes < 1024.0:
                return f"{size_in_bytes:.1f} {unit}"
            size_in_bytes /= 1024.0
        return f"{size_in_bytes:.1f} PB"

    def get_human_used(self, obj):
        return self._format_bytes(obj.storage_used_bytes)

    def get_human_limit(self, obj):
        return self._format_bytes(obj.storage_limit_bytes)
