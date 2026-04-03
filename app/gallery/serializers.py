"""
Serializers for the Gallery API View.
"""
from rest_framework import serializers
from core.models import Gallery, Image


# ==========================================
# 1. GALLERY MANAGEMENT
# ==========================================

class GallerySerializer(serializers.ModelSerializer):
    """Serializer for managing client galleries."""

    class Meta:
        model = Gallery
        fields = [
            'id', 'title', 'slug', 'is_public',
            'allow_downloads', 'expires_at', 'gallery_pin',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'gallery_pin': {'write_only': True, 'required': False}
        }


# ==========================================
# 2. IMAGE UPLOAD & HANDLING
# ==========================================

class ImageSerializer(serializers.ModelSerializer):
    """Serializer for uploading and managing image files."""

    class Meta:
        model = Image
        fields = ['id', 'gallery', 'title', 'image', 'order', 'created_at']
        read_only_fields = ['id', 'created_at']
        extra_kwargs = {
            # SECURITY: An Image database row is completely useless without the actual file
            'image': {'required': True}
        }
















# """
# Serializers for the gallery API View.
# """
# from rest_framework import serializers
# from core.models import Gallery

# class GallerySerializer(serializers.ModelSerializer):
#     """Serializer for galleries."""

#     class Meta:
#         model = Gallery
#         # SAAS UPGRADE: Included the new UI toggles and the PIN
#         fields = [
#             'id', 'title', 'slug', 'is_public',
#             'allow_downloads', 'expires_at', 'gallery_pin',
#             'created_at', 'updated_at'
#         ]
#         read_only_fields = ['id', 'created_at', 'updated_at']
#         extra_kwargs = {
#             # SECURITY: The PIN can be set, but never read back by the frontend
#             'gallery_pin': {'write_only': True, 'required': False}
#         }
