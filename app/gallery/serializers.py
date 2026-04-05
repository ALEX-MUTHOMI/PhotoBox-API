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

    # THE FIX: Tell DRF to stop doing basic pre-checks. Pass the raw file to our View.
    image = serializers.FileField(required=True)

    class Meta:
        model = Image

        # INTEGRATED: Added file_size_bytes so React can build the Quota Progress Bar
        fields = ['id', 'gallery', 'title', 'image', 'file_size_bytes', 'order', 'created_at']

        # THE VAULT: Hackers cannot manipulate their file size math to bypass billing.
        read_only_fields = ['id', 'created_at', 'file_size_bytes']

    def update(self, instance, validated_data):
        """
        SECURITY (Lateral Movement Shield):
        Once an image is uploaded to a gallery, it is permanently welded to that gallery.
        This prevents hackers from using a PATCH request to move an image into a
        different tenant's workspace, bypassing the perform_create security shields.
        """
        # Violently rip the 'gallery' field out of the payload if they try to update it
        validated_data.pop('gallery', None)

        # Also prevent them from swapping the actual binary file via PATCH to bypass the Malware Shield
        validated_data.pop('image', None)

        return super().update(instance, validated_data)
