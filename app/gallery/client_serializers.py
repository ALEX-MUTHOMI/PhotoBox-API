"""Serializers for public gallery responses and client-facing payloads."""

from rest_framework import serializers
from django.utils.html import escape, strip_tags
from urllib.parse import urlparse
from drf_spectacular.utils import extend_schema_field

from gallery.client_auth import normalize_gallery_email
from gallery.models import (
    Event,
    FavoriteSelection,
    GalleryAccessRole,
    Photo,
    Scene,
    VisibilityChoices,
)


def safe_client_text(value: str | None) -> str:
    return escape(strip_tags(value or ""))


def safe_client_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlparse(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return str(value)


class MagicLinkRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return normalize_gallery_email(value)


class MagicLinkConsumeSerializer(serializers.Serializer):
    token = serializers.CharField()


class GuestAccessSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True)
    # PIN may be omitted so the view can return 403 (plan: no cookies, no hash).
    # If a PIN is sent, it must be at least 6 characters (4-digit ATM PINs → 400).
    pin = serializers.CharField(
        required=False,
        allow_blank=True,
        write_only=True,
        max_length=128,
    )

    def validate_email(self, value):
        if not value:
            return None
        return normalize_gallery_email(value)

    def validate_pin(self, value):
        pin = str(value or "")
        if not pin:
            return ""
        if len(pin) < 6:
            raise serializers.ValidationError("PIN must be at least 6 characters.")
        return pin


class GalleryPublicPhotoSerializer(serializers.ModelSerializer):
    delivery_url = serializers.SerializerMethodField()
    delivery_url_tile = serializers.SerializerMethodField()
    delivery_url_lightbox = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    aspect_ratio = serializers.SerializerMethodField()
    original_filename = serializers.SerializerMethodField()

    def get_original_filename(self, obj):
        return safe_client_text(obj.original_filename)

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_delivery_url(self, obj):
        return safe_client_url(obj.delivery_url)

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_delivery_url_tile(self, obj):
        return safe_client_url(obj.delivery_url_tile)

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_delivery_url_lightbox(self, obj):
        return safe_client_url(obj.delivery_url_lightbox)

    @extend_schema_field(serializers.FloatField(allow_null=True))
    def get_aspect_ratio(self, obj):
        return obj.aspect_ratio

    @extend_schema_field(serializers.URLField(allow_null=True))
    def get_download_url(self, obj):
        allow = self.context.get("allow_downloads", True)
        role = self.context.get("access_role")
        if allow is False:
            return None
        if role == GalleryAccessRole.GUEST and allow is False:
            return None
        # Guests never get original download URLs when downloads disabled;
        # when enabled, still omit from default public photo payload (use download endpoint).
        return None

    class Meta:
        model = Photo
        fields = [
            'id',
            'original_filename',
            'delivery_url',
            'delivery_url_tile',
            'delivery_url_lightbox',
            'download_url',
            'aspect_ratio',
            'width',
            'height',
            'blurhash',
            'uploaded_at',
        ]


class GalleryPublicSceneSerializer(serializers.ModelSerializer):
    photos = GalleryPublicPhotoSerializer(many=True, read_only=True)
    title = serializers.SerializerMethodField()
    has_more_photos = serializers.SerializerMethodField()

    def get_title(self, obj):
        return safe_client_text(obj.title)

    def get_has_more_photos(self, obj):
        limit = int(self.context.get("photos_per_scene_limit", 100))
        # Prefetch loads limit+1 rows so we can detect overflow without a COUNT.
        return len(list(obj.photos.all())) > limit

    def to_representation(self, instance):
        data = super().to_representation(instance)
        limit = int(self.context.get("photos_per_scene_limit", 100))
        data["photos"] = data.get("photos", [])[:limit]
        return data

    class Meta:
        model = Scene
        fields = ['id', 'title', 'display_order', 'photos', 'has_more_photos']


class GalleryPublicSerializer(serializers.ModelSerializer):
    scenes = GalleryPublicSceneSerializer(many=True, read_only=True)
    title = serializers.SerializerMethodField()
    cover_image_url = serializers.SerializerMethodField()
    cover_photo = serializers.SerializerMethodField()
    share_code = serializers.CharField(read_only=True)

    def get_title(self, obj):
        return safe_client_text(obj.title)

    def get_cover_image_url(self, obj):
        return safe_client_url(obj.cover_image_url)

    def get_cover_photo(self, obj):
        return safe_client_url(obj.cover_photo)

    class Meta:
        model = Event
        fields = [
            'id',
            'share_code',
            'title',
            'event_type',
            'event_date',
            'cover_image_url',
            'cover_photo',
            'typography_theme',
            'color_theme',
            'slug',
            'expires_at',
            'allow_downloads',
            'scenes',
        ]


class FavoriteSelectionWriteSerializer(serializers.Serializer):
    photo_id = serializers.UUIDField()
    notes = serializers.CharField(
        allow_blank=True,
        max_length=2000,
        required=False,
    )

    def validate(self, attrs):
        gallery = self.context['gallery']
        role = self.context['role']

        allowed_visibility = [VisibilityChoices.PUBLIC]
        if role == GalleryAccessRole.CLIENT:
            allowed_visibility.append(VisibilityChoices.CLIENT_ONLY)

        photo = (
            Photo.objects
            .select_related('scene')
            .filter(
                id=attrs['photo_id'],
                scene__event=gallery,
                status='READY',
                visibility__in=allowed_visibility,
            )
            .first()
        )
        if photo is None:
            raise serializers.ValidationError(
                {'photo_id': 'Photo not found in this gallery.'}
            )

        attrs['photo'] = photo
        attrs.setdefault('notes', '')
        return attrs


class FavoriteSelectionSerializer(serializers.ModelSerializer):
    photo_id = serializers.UUIDField(read_only=True)
    notes = serializers.SerializerMethodField()

    def get_notes(self, obj):
        return safe_client_text(obj.notes)

    class Meta:
        model = FavoriteSelection
        fields = ['id', 'photo_id', 'notes', 'created_at']
