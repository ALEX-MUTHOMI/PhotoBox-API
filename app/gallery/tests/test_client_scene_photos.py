"""Tests for nested client scene photo keyset pagination."""
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
    CLOUDINARY_CLOUD_NAME="photobox-test",
    CLOUDFLARE_R2_DOMAIN="media.example.test",
)
class ClientScenePhotoKeysetTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="scene-keyset@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.owner, business_name="Keyset Studio"
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Keyset Gallery",
            slug="keyset-gallery",
            is_published=True,
        )
        self.scene = Scene.objects.create(event=self.gallery, title="Main")
        Photo.objects.bulk_create(
            [
                Photo(
                    scene=self.scene,
                    original_filename=f"p{i:03d}.jpg",
                    file_size_bytes=100,
                    r2_object_key=f"raw/p{i:03d}.jpg",
                    status="READY",
                    is_processed=True,
                )
                for i in range(25)
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

    def test_scene_photos_keyset_pages_without_offset(self):
        url = reverse(
            "gallery_public:scene-photos",
            args=[self.gallery.id, self.scene.id],
        )
        first = self.client.get(url, {"page_size": 10})
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(len(first.data["results"]), 10)
        self.assertTrue(first.data["has_more"])
        self.assertIsNotNone(first.data["next_cursor"])

        second = self.client.get(
            url, {"page_size": 10, "cursor": first.data["next_cursor"]}
        )
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        first_ids = {row["id"] for row in first.data["results"]}
        second_ids = {row["id"] for row in second.data["results"]}
        self.assertFalse(first_ids & second_ids)

    def test_stolen_cursor_from_rival_gallery_returns_empty(self):
        rival_owner = User.objects.create_user(
            email="rival-scene@example.com",
            password="StrongPassword123!",
            name="Rival",
            accepted_terms=True,
        )
        rival_ws = Workspace.objects.create(user=rival_owner, business_name="Rival")
        rival_gallery = Event.objects.create(
            workspace=rival_ws,
            title="Rival",
            slug="rival-scene",
            is_published=True,
        )
        rival_scene = Scene.objects.create(event=rival_gallery, title="Secret")
        secret = Photo.objects.create(
            scene=rival_scene,
            original_filename="secret.jpg",
            file_size_bytes=100,
            status="READY",
        )
        from gallery.pagination import encode_photo_keyset_cursor

        cursor = encode_photo_keyset_cursor(secret.uploaded_at, secret.id)
        url = reverse(
            "gallery_public:scene-photos",
            args=[self.gallery.id, self.scene.id],
        )
        response = self.client.get(url, {"cursor": cursor, "page_size": 10})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in response.data["results"]}
        self.assertNotIn(str(secret.id), ids)
