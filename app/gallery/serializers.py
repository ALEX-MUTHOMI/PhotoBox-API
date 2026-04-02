"""
Serializers for the gallery API View.
"""
from rest_framework import serializers
from core.models import Gallery


class GallerySerializer(serializers.ModelSerializer):
    """Serializer for galleries."""

    class Meta:
        model = Gallery
        # We only expose the safe fields. We do NOT expose the client_password here!
        fields = ['id', 'title', 'slug', 'is_public', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
