"""
Serializers for the Gallery API View (The Pixieset Standard).
"""
import secrets
from rest_framework import serializers
from gallery.models import Event, Scene, Photo


# ==========================================
# 1. PIXIESET STANDARD: EVENT (The Collection)
# ==========================================

class EventSerializer(serializers.ModelSerializer):
    """Serializer for managing the primary Event (e.g., The Wedding)."""

    # We expose 'gallery_pin' for the client to set a password, but we NEVER return it.
    gallery_pin = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = Event
        fields = [
            'id', 'title', 'event_type', 'event_date', 'cover_image_url',
            'cover_photo', 'typography_theme', 'color_theme',
            'slug', 'is_published', 'expires_at', 'gallery_pin', 'created_at',
            'client_email', 'client_name',
        ]
        read_only_fields = ['id', 'created_at', 'slug']

    def create(self, validated_data):
        """Intercept creation to safely hash the PIN and auto-generate the cryptographic slug."""
        raw_pin = validated_data.pop('gallery_pin', None)

        # IDEMPOTENCY DEFENSE: Generate a highly random slug to prevent database collisions
        # even if a user creates two events named "The Wedding".
        # E.g., 'the-wedding-a4b8f9'
        safe_title = validated_data.get('title', 'event').lower().replace(' ', '-')
        crypto_suffix = secrets.token_hex(4)
        validated_data['slug'] = f"{safe_title}-{crypto_suffix}"

        event = super().create(validated_data)

        # Pass the raw PIN to the cryptographic hasher function in the Model
        if raw_pin:
            event.set_pin(raw_pin)

        return event

    def update(self, instance, validated_data):
        """Intercept updates to safely re-hash the PIN if it's being changed."""
        raw_pin = validated_data.pop('gallery_pin', None)

        # SECURITY (Lateral Movement Shield): Violently rip out `workspace` if they try to PATCH it
        validated_data.pop('workspace', None)

        event = super().update(instance, validated_data)

        if raw_pin is not None:
            # If they pass '', it clears the PIN. If they pass a string, it hashes it.
            event.set_pin(raw_pin if raw_pin else None)

        return event


# ==========================================
# 2. PIXIESET STANDARD: THE STAGE (Scenes / Tabs)
# ==========================================

class SceneSerializer(serializers.ModelSerializer):
    """Serializer for managing sub-categories (e.g., Ceremony, Reception)."""

    class Meta:
        model = Scene
        fields = ['id', 'event', 'title', 'display_order', 'visibility']
        read_only_fields = ['id']

    def update(self, instance, validated_data):
        """
        SECURITY (Ghost Shield):
        Prevent a hacker from taking a Scene from Event A and maliciously
        moving it into a competitor's Event B via PATCH.
        """
        validated_data.pop('event', None)
        return super().update(instance, validated_data)


# ==========================================
# 3. FAST LANE / DELIVERY LAYER: ASSET SERIALIZER
# ==========================================

class PhotoFastLaneSerializer(serializers.ModelSerializer):
    """
    Serializer for the Fast Lane and client delivery.

    Delivery fields (read-only, computed from model properties):
      - delivery_url:  Cloudinary Fetch proxy URL (WebP, auto quality)
      - download_url:  R2 presigned GET URL (hard 900s expiry)
      - aspect_ratio:  width/height for zero-layout-shift masonry grids

    DO NOT use this for 5,000 Wedding RAWs. Use the Ingestion App (Heavy Lane) for that.
    """

    # THE FIX: Tell DRF to stop doing basic pre-checks. Pass the raw file to our View.
    image_file = serializers.FileField(required=True)

    # PHASE 3: Delivery layer — all read-only, derived from model properties
    delivery_url = serializers.ReadOnlyField()
    download_url = serializers.ReadOnlyField()
    aspect_ratio = serializers.ReadOnlyField()

    # Backward compat aliases (deprecated — will be removed in v2)
    r2_download_url = serializers.ReadOnlyField()
    cloudinary_thumbnail_url = serializers.ReadOnlyField()

    class Meta:
        model = Photo
        fields = [
            'id', 'scene', 'visibility', 'image_file', 'original_filename', 'file_size_bytes',
            'is_processed', 'status', 'blurhash',
            # Delivery layer
            'delivery_url', 'download_url', 'aspect_ratio',
            'width', 'height',
            # Backward compat aliases
            'r2_download_url', 'cloudinary_thumbnail_url',
            'uploaded_at',
        ]

        # THE VAULT: Hackers cannot manipulate file size math to bypass billing,
        # or inject a fake r2_object_key to access other tenants' files.
        read_only_fields = [
            'id', 'uploaded_at', 'file_size_bytes', 'is_processed', 'status',
            'blurhash', 'width', 'height', 'aspect_ratio',
            'delivery_url', 'download_url',
            'r2_download_url', 'cloudinary_thumbnail_url',
            'original_filename',
        ]

    def update(self, instance, validated_data):
        """
        SECURITY (Lateral Movement Shield):
        Once an image is uploaded to a Scene, it belongs there.
        Do not let it be reassigned to a different tenant via PATCH.
        """
        validated_data.pop('scene', None)

        # Also prevent them from swapping the actual binary file via PATCH to bypass the Malware Shield
        validated_data.pop('image_file', None)

        return super().update(instance, validated_data)
