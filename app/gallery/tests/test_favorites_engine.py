from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError
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
    FavoriteSelection,
    GalleryAccessRole,
    GalleryAccessSession,
    Photo,
    Scene,
    VisibilityChoices,
)
from gallery.throttles import FavoriteSelectionThrottle


User = get_user_model()


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
)
class FavoritesEngineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.photographer_client = APIClient()
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Proof Studio",
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Favorites Gallery",
            slug="favorites-gallery",
            is_published=True,
        )
        self.public_scene = Scene.objects.create(
            event=self.gallery,
            title="Ceremony",
            visibility=VisibilityChoices.PUBLIC,
        )
        self.client_scene = Scene.objects.create(
            event=self.gallery,
            title="Private Portraits",
            visibility=VisibilityChoices.CLIENT_ONLY,
        )
        self.public_photo = Photo.objects.create(
            scene=self.public_scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="public.jpg",
            file_size_bytes=120,
            status="READY",
            is_processed=True,
            r2_object_key="gallery/public.jpg",
        )
        self.client_only_photo = Photo.objects.create(
            scene=self.client_scene,
            visibility=VisibilityChoices.CLIENT_ONLY,
            original_filename="client.jpg",
            file_size_bytes=180,
            status="READY",
            is_processed=True,
            r2_object_key="gallery/client.jpg",
        )
        self.other_gallery = Event.objects.create(
            workspace=self.workspace,
            title="Other Gallery",
            slug="other-gallery",
            is_published=True,
        )
        self.other_scene = Scene.objects.create(
            event=self.other_gallery,
            title="Other Scene",
            visibility=VisibilityChoices.PUBLIC,
        )
        self.other_photo = Photo.objects.create(
            scene=self.other_scene,
            visibility=VisibilityChoices.PUBLIC,
            original_filename="other.jpg",
            file_size_bytes=90,
            status="READY",
            is_processed=True,
            r2_object_key="gallery/other.jpg",
        )
        self.photographer_client.force_authenticate(user=self.user)

    def _set_gallery_cookies(self, role, email):
        if role == GalleryAccessRole.CLIENT:
            ClientAllowlist.objects.get_or_create(gallery=self.gallery, email=email)
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email=email,
            role=role,
        )
        self.client.cookies["gallery_access"] = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email=email,
            role=role,
        )
        self.client.cookies["gallery_session"] = encode_gallery_access_session_cookie(session.id)
        return session

    def test_favorite_selection_unique_constraint_blocks_duplicates(self):
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="bride@example.com",
            role=GalleryAccessRole.CLIENT,
        )
        FavoriteSelection.objects.create(session=session, photo=self.public_photo)

        with self.assertRaises(IntegrityError):
            FavoriteSelection.objects.create(session=session, photo=self.public_photo)

    def test_client_can_add_and_remove_favorite(self):
        session = self._set_gallery_cookies(
            GalleryAccessRole.CLIENT,
            "bride@example.com",
        )

        response = self.client.post(
            reverse("gallery_public:favorites", args=[self.gallery.id]),
            {"photo_id": str(self.client_only_photo.id), "notes": "Album pick"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        selection = FavoriteSelection.objects.get(session=session, photo=self.client_only_photo)
        self.assertEqual(selection.notes, "Album pick")

        delete_response = self.client.delete(
            reverse("gallery_public:favorite-detail", args=[self.gallery.id, self.client_only_photo.id]),
        )

        self.assertEqual(delete_response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            FavoriteSelection.objects.filter(session=session, photo=self.client_only_photo).exists()
        )

    def test_guest_cannot_favorite_client_only_photo(self):
        self._set_gallery_cookies(
            GalleryAccessRole.GUEST,
            "guest@example.com",
        )

        response = self.client.post(
            reverse("gallery_public:favorites", args=[self.gallery.id]),
            {"photo_id": str(self.client_only_photo.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("photo_id", response.data)

    def test_favorite_request_rejects_photo_from_different_gallery(self):
        self._set_gallery_cookies(
            GalleryAccessRole.CLIENT,
            "bride@example.com",
        )

        response = self.client.post(
            reverse("gallery_public:favorites", args=[self.gallery.id]),
            {"photo_id": str(self.other_photo.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(FavoriteSelection.objects.count(), 0)

    def test_favorites_summary_groups_by_photo_for_photographer(self):
        session_one = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="bride@example.com",
            role=GalleryAccessRole.CLIENT,
        )
        session_two = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="guest@example.com",
            role=GalleryAccessRole.GUEST,
        )
        FavoriteSelection.objects.create(
            session=session_one,
            photo=self.public_photo,
            notes="Use for thank-you cards",
        )
        FavoriteSelection.objects.create(
            session=session_two,
            photo=self.public_photo,
        )

        response = self.photographer_client.get(
            reverse("gallery_public:favorites-summary", args=[self.gallery.id]),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["gallery_id"], str(self.gallery.id))
        self.assertEqual(len(response.data["favorites"]), 1)
        summary = response.data["favorites"][0]
        self.assertEqual(summary["photo_id"], str(self.public_photo.id))
        self.assertEqual(summary["favorite_count"], 2)
        self.assertEqual(
            {selection["email"] for selection in summary["selections"]},
            {"bride@example.com", "guest@example.com"},
        )

    @patch.object(FavoriteSelectionThrottle, "THROTTLE_RATES", {"favorite_selection": "1/minute"})
    def test_favorite_selection_is_rate_limited(self):
        self._set_gallery_cookies(
            GalleryAccessRole.CLIENT,
            "bride@example.com",
        )

        first = self.client.post(
            reverse("gallery_public:favorites", args=[self.gallery.id]),
            {"photo_id": str(self.public_photo.id)},
            format="json",
        )
        second = self.client.post(
            reverse("gallery_public:favorites", args=[self.gallery.id]),
            {"photo_id": str(self.client_only_photo.id)},
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
