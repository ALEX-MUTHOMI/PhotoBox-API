"""
Serializers for the user API View.
"""
from django.contrib.auth import get_user_model
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError

class UserSerializer(serializers.ModelSerializer):
    """Serializer for the user object with built-in Anti-Fraud."""
    old_password = serializers.CharField(write_only=True, required=False)

    # 🛡️ SECURITY: Anti-Bot Token (Required for creation, ignored on profile updates)
    cf_turnstile_response = serializers.CharField(write_only=True, required=False)

    class Meta:
        model = get_user_model()
        fields = [
            'email', 'password', 'old_password', 'name',
            'subscription_tier', 'storage_limit_gb',
            'accepted_terms', 'cf_turnstile_response'
        ]
        # THE VAULT: Hackers cannot upgrade their own billing tier
        read_only_fields = ['subscription_tier', 'storage_limit_gb']
        extra_kwargs = {
            'password': {'write_only': True, 'min_length': 5},
            'accepted_terms': {'required': True}
        }

    def validate_accepted_terms(self, value):
        """SECURITY: Ensure the user actually checked the compliance box."""
        if not value:
            raise serializers.ValidationError("You must accept the Terms of Service.")
        return value

    def validate_email(self, value):
        """ANTI-FRAUD: The 'Infinite Free Tier' killer. Strips + and . from Gmail."""
        email = value.lower().strip()
        if '@' in email:
            local_part, domain = email.split('@')
            if domain in ['gmail.com', 'googlemail.com']:
                local_part = local_part.split('+')[0]
                local_part = local_part.replace('.', '')
            return f"{local_part}@{domain}"
        return email

    def validate_password(self, value):
        """UX & SECURITY: Clean password entropy validation."""
        try:
            validate_password(value)
        except DjangoValidationError as e:
            raise serializers.ValidationError(list(e.messages))
        return value

    def create(self, validated_data):
        """Create a user after stripping non-database fields."""
        validated_data.pop('cf_turnstile_response', None)
        validated_data.pop('old_password', None)
        return get_user_model().objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        """Securely update profile, enforcing password verification."""
        password = validated_data.pop('password', None)
        old_password = validated_data.pop('old_password', None)
        validated_data.pop('cf_turnstile_response', None) # Just in case

        if password:
            if not old_password:
                raise serializers.ValidationError({'old_password': 'Old password is required to change password.'})
            if not instance.check_password(old_password):
                raise serializers.ValidationError({'old_password': 'Old password is incorrect.'})
            instance.set_password(password)

        return super().update(instance, validated_data)
