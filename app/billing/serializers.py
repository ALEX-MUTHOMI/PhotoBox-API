"""Serializers for billing subscription status and related API responses."""

from rest_framework import serializers
from .models import Subscription


class SubscriptionSerializer(serializers.ModelSerializer):
    """
    OUTBOUND: Photographer subscription read model.
    Storage figures come from the Workspace ledger — the only counter uploads write.
    """
    storage_limit_bytes = serializers.SerializerMethodField()
    storage_used_bytes = serializers.SerializerMethodField()
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
            'human_limit',
        ]
        read_only_fields = fields

    def _workspace(self, obj):
        return obj.user.workspace

    def get_storage_used_bytes(self, obj):
        return self._workspace(obj).storage_used_bytes

    def get_storage_limit_bytes(self, obj):
        return self._workspace(obj).storage_limit_bytes

    def get_percentage_used(self, obj):
        limit = self.get_storage_limit_bytes(obj)
        if limit == 0:
            return 100
        used = self.get_storage_used_bytes(obj)
        return round((used / limit) * 100, 2)

    def _format_bytes(self, size_in_bytes):
        size = float(size_in_bytes)
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} PB"

    def get_human_used(self, obj):
        return self._format_bytes(self.get_storage_used_bytes(obj))

    def get_human_limit(self, obj):
        return self._format_bytes(self.get_storage_limit_bytes(obj))
