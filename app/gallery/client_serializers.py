from rest_framework import serializers

from gallery.client_auth import normalize_gallery_email
from gallery.models import Event, Photo, Scene


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
            'slug',
            'expires_at',
            'scenes',
        ]
