"""Public gallery detail must not dump unbounded photo lists."""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.client_auth import (
    encode_gallery_access_session_cookie,
    issue_gallery_access_token,
)
from gallery.models import (
    ClientAllowlist,
    Event,
    GalleryAccessRole,
    GalleryAccessSession,
    Photo,
    Scene,
)


User = get_user_model()


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
    GALLERY_PUBLIC_PHOTOS_PER_SCENE=5,
    CLOUDINARY_CLOUD_NAME="photobox-test",
    CLOUDFLARE_R2_DOMAIN="media.example.test",
)
class PublicGalleryPhotoBoundTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="bound-owner@example.com",
            password="StrongPassword123!",
            name="Bound Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.owner,
            business_name="Bound Studio",
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Bound Gallery",
            slug="bound-gallery",
            is_published=True,
        )
        self.scene = Scene.objects.create(event=self.gallery, title="Main")
        Photo.objects.bulk_create(
            [
                Photo(
                    scene=self.scene,
                    original_filename=f"p{i}.jpg",
                    file_size_bytes=100,
                    r2_object_key=f"raw/p{i}.jpg",
                    status="READY",
                    is_processed=True,
                )
                for i in range(12)
            ]
        )
        self.client = APIClient()
        ClientAllowlist.objects.create(gallery=self.gallery, email="client@example.com")
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="client@example.com",
            role=GalleryAccessRole.CLIENT,
        )
        self.client.cookies["gallery_access"] = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email=session.email,
            role=session.role,
        )
        self.client.cookies["gallery_session"] = encode_gallery_access_session_cookie(
            session.id
        )

    def test_public_detail_caps_photos_per_scene_and_flags_has_more(self):
        response = self.client.get(
            reverse("gallery_public:detail", args=[self.gallery.id])
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        scenes = response.data["gallery"]["scenes"]
        self.assertEqual(len(scenes), 1)
        self.assertEqual(len(scenes[0]["photos"]), 5)
        self.assertTrue(scenes[0]["has_more_photos"])
