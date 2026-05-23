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
    Event,
    GalleryAccessRole,
    GalleryAccessSession,
    Photo,
    Scene,
)


User = get_user_model()


@override_settings(
    JWT_SIGNING_KEY="test-signing-key-that-is-at-least-32-bytes-long!!",
)
class AuthDomainBoundaryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.owner,
            business_name="Boundary Studio",
        )
        self.gallery = Event.objects.create(
            workspace=self.workspace,
            title="Boundary Gallery",
            slug="boundary-gallery",
            is_published=True,
        )
        self.scene = Scene.objects.create(event=self.gallery, title="Main")
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename="client-visible.jpg",
            file_size_bytes=1024,
            status="READY",
            is_processed=True,
        )

        self.gallery_client = APIClient()
        session = GalleryAccessSession.objects.create(
            gallery=self.gallery,
            email="client@example.com",
            role=GalleryAccessRole.CLIENT,
        )
        self.gallery_client.cookies["gallery_access"] = issue_gallery_access_token(
            gallery_id=self.gallery.id,
            email=session.email,
            role=session.role,
        )
        self.gallery_client.cookies["gallery_session"] = (
            encode_gallery_access_session_cookie(session.id)
        )

        self.photographer_client = APIClient()
        self.photographer_client.force_authenticate(user=self.owner)

    def test_gallery_client_token_cannot_list_dashboard_events(self):
        response = self.gallery_client.get(reverse("gallery:event-list"))

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_photographer_identity_cannot_use_client_favorite_mutation(self):
        response = self.photographer_client.post(
            reverse("gallery_public:favorites", args=[self.gallery.id]),
            {"photo_id": str(self.photo.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
