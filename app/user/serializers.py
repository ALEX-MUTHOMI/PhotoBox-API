"""
Serializers for the user API View.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers


class UserSerializer(serializers.ModelSerializer):
    """Serializer for the user object."""
    # Add a temporary field that never gets saved to the database
    old_password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = get_user_model()
        fields = ['email', 'password', 'old_password', 'name', 'subscription_tier', 'storage_limit_gb']

        # THE VAULT: Hackers cannot upgrade their own billing
        read_only_fields = ['subscription_tier', 'storage_limit_gb']

        extra_kwargs = {
            'password': {'write_only': True, 'min_length': 5}
        }

    def create(self, validated_data):
        """Create and return a user with encrypted password."""
        # Remove old_password from validated data just in case it was passed during creation
        validated_data.pop('old_password', None)
        return get_user_model().objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        """Securely update and return user profile."""
        password = validated_data.pop('password', None)
        old_password = validated_data.pop('old_password', None)

        # SECURITY: If they are trying to change their password, enforce verification
        if password:
            if not old_password:
                raise serializers.ValidationError(
                    {'old_password': 'Old password is required to set a new password.'}
                )
            if not instance.check_password(old_password):
                raise serializers.ValidationError(
                    {'old_password': 'Old password is incorrect.'}
                )
            instance.set_password(password)

        return super().update(instance, validated_data)
