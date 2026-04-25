from rest_framework import serializers

from gallery.client_auth import normalize_gallery_email
from gallery.models import (
    Event,
    FavoriteSelection,
    GalleryAccessRole,
    Photo,
    Scene,
    VisibilityChoices,
)


class MagicLinkRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return normalize_gallery_email(value)


class MagicLinkConsumeSerializer(serializers.Serializer):
    token = serializers.CharField()


class GuestAccessSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        return normalize_gallery_email(value)


class GalleryPublicPhotoSerializer(serializers.ModelSerializer):
    delivery_url = serializers.ReadOnlyField()
    aspect_ratio = serializers.ReadOnlyField()

    class Meta:
        model = Photo
        fields = [
            'id',
            'original_filename',
            'delivery_url',
            'aspect_ratio',
            'width',
            'height',
            'blurhash',
            'uploaded_at',
        ]


class GalleryPublicSceneSerializer(serializers.ModelSerializer):
    photos = GalleryPublicPhotoSerializer(many=True, read_only=True)

    class Meta:
        model = Scene
        fields = ['id', 'title', 'display_order', 'photos']


class GalleryPublicSerializer(serializers.ModelSerializer):
    scenes = GalleryPublicSceneSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = [
            'id',
            'title',
            'event_type',
            'event_date',
            'cover_image_url',
            'cover_photo',
            'typography_theme',
            'color_theme',
            'slug',
            'expires_at',
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

    class Meta:
        model = FavoriteSelection
        fields = ['id', 'photo_id', 'notes', 'created_at']
