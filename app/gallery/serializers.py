"""
Serializers for the Gallery API View (The Pixieset Standard).
"""
import re
import secrets

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.text import slugify
from rest_framework import serializers
from drf_spectacular.utils import extend_schema_field

from core.models import Workspace, validate_png_watermark
from gallery.client_auth import normalize_gallery_email
from gallery.models import ClientAllowlist, Event, Photo, Scene
from gallery.phone import normalize_e164_phone


GALLERY_PIN_MIN_LENGTH = 6


# ==========================================
# 1. PIXIESET STANDARD: EVENT (The Collection)
# ==========================================

class EventSerializer(serializers.ModelSerializer):
    """Serializer for managing the primary Event (e.g., The Wedding)."""

    # We expose 'gallery_pin' for the client to set a password, but we NEVER return it.
    gallery_pin = serializers.CharField(write_only=True, required=False, allow_blank=True)
    share_code = serializers.CharField(read_only=True)
    pin_set = serializers.SerializerMethodField()
    public_url = serializers.SerializerMethodField()
    whatsapp_text = serializers.SerializerMethodField()

    class Meta:
        model = Event
        fields = [
            'id', 'title', 'event_type', 'event_date', 'cover_image_url',
            'cover_photo', 'typography_theme', 'color_theme',
            'slug', 'share_code', 'is_published', 'expires_at', 'allow_downloads',
            'gallery_pin', 'pin_set', 'public_url', 'whatsapp_text', 'created_at',
            'client_email', 'client_name', 'client_phone',
        ]
        read_only_fields = [
            'id', 'created_at', 'slug', 'expires_at', 'share_code',
            'pin_set', 'public_url', 'whatsapp_text',
        ]

    @extend_schema_field(serializers.BooleanField())
    def get_pin_set(self, obj):
        return bool(obj._hashed_pin)

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_public_url(self, obj):
        from django.conf import settings

        frontend = getattr(settings, 'FRONTEND_URL', 'https://app.photobox.app').rstrip('/')
        if not obj.share_code:
            return None
        return f"{frontend}/g/{obj.share_code}"

    @extend_schema_field(serializers.CharField())
    def get_whatsapp_text(self, obj):
        url = self.get_public_url(obj)
        if not url:
            return ''
        return f"View your photos: {url}"

    def validate_gallery_pin(self, value):
        if value in (None, ''):
            return value
        if len(str(value)) < GALLERY_PIN_MIN_LENGTH:
            raise serializers.ValidationError(
                f"PIN must be at least {GALLERY_PIN_MIN_LENGTH} characters."
            )
        return value

    def validate_client_phone(self, value):
        return normalize_e164_phone(value)

    def create(self, validated_data):
        """Intercept creation to safely hash the PIN and auto-generate the cryptographic slug."""
        raw_pin = validated_data.pop('gallery_pin', None)

        # IDEMPOTENCY DEFENSE: Generate a highly random slug to prevent database collisions
        # even if a user creates two events named "The Wedding".
        # E.g., 'the-wedding-a4b8f9'
        safe_title = slugify(validated_data.get('title', 'event')) or 'event'
        crypto_suffix = secrets.token_hex(4)
        validated_data['slug'] = f"{safe_title}-{crypto_suffix}"

        event = super().create(validated_data)

        # Pass the raw PIN to the cryptographic hasher function in the Model
        if raw_pin:
            event.set_pin(raw_pin)
            self._pin_plaintext_once = str(raw_pin)

        return event

    def update(self, instance, validated_data):
        """Intercept updates to safely re-hash the PIN if it's being changed."""
        raw_pin = validated_data.pop('gallery_pin', None)

        # SECURITY (Lateral Movement Shield): Violently rip out `workspace` if they try to PATCH it
        validated_data.pop('workspace', None)

        publishing = validated_data.get('is_published') is True and not instance.is_published

        # Apply PIN before publish check so one PATCH can set PIN + publish.
        if raw_pin is not None:
            instance.set_pin(raw_pin if raw_pin else None)
            if raw_pin:
                self._pin_plaintext_once = str(raw_pin)

        if publishing and not instance.has_pin:
            raise serializers.ValidationError(
                {'gallery_pin': 'A gallery PIN (min 6 characters) is required before publish.'}
            )

        return super().update(instance, validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        pin_once = getattr(self, '_pin_plaintext_once', None)
        if pin_once:
            data['gallery_pin_once'] = pin_once
        return data


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
    delivery_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    aspect_ratio = serializers.SerializerMethodField()

    # Backward compat aliases (deprecated — will be removed in v2)
    r2_download_url = serializers.SerializerMethodField()
    cloudinary_thumbnail_url = serializers.SerializerMethodField()

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_delivery_url(self, obj):
        return obj.delivery_url

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_download_url(self, obj):
        return obj.download_url

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_aspect_ratio(self, obj):
        return obj.aspect_ratio

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_r2_download_url(self, obj):
        return obj.r2_download_url

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_cloudinary_thumbnail_url(self, obj):
        return obj.cloudinary_thumbnail_url

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


_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_BRAND_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ClientAllowlistSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClientAllowlist
        fields = ["id", "email", "phone", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_email(self, value):
        email = normalize_gallery_email(value)
        if not email:
            raise serializers.ValidationError("Email is required.")
        return email

    def validate_phone(self, value):
        return normalize_e164_phone(value)


class WorkspaceBrandingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = [
            "business_name",
            "brand_color",
            "logo",
            "watermark_logo",
            "watermark_opacity",
            "custom_domain",
        ]
        extra_kwargs = {
            "custom_domain": {"validators": []},
        }

    def validate_brand_color(self, value):
        if not _BRAND_COLOR_RE.match(value or ""):
            raise serializers.ValidationError("Brand color must be a #RRGGBB hex value.")
        return value

    def validate_custom_domain(self, value):
        if value in (None, ""):
            return None
        raw = str(value).strip().lower()
        if "://" in raw or "/" in raw or ":" in raw or " " in raw:
            raise serializers.ValidationError(
                "Custom domain must be a hostname, not a URL."
            )
        if not _HOSTNAME_RE.match(raw):
            raise serializers.ValidationError("Enter a valid hostname.")
        return raw

    def validate_watermark_logo(self, value):
        if not value:
            return value
        try:
            validate_png_watermark(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages)
        return value
