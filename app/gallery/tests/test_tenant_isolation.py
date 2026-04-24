from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Photo, Scene


User = get_user_model()


class TenantIsolationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.owner = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.owner_workspace = Workspace.objects.create(
            user=self.owner,
            business_name="Owner Studio",
        )
        self.owner_event = Event.objects.create(
            workspace=self.owner_workspace,
            title="Owner Event",
            slug="owner-event",
        )
        self.owner_scene = Scene.objects.create(
            event=self.owner_event,
            title="Owner Scene",
        )
        self.owner_photo = Photo.objects.create(
            scene=self.owner_scene,
            original_filename="owner.jpg",
            file_size_bytes=1024,
            status="READY",
            is_processed=True,
            r2_object_key="fast-lane/tenant_owner/photo-owner/owner.jpg",
        )

        self.rival = User.objects.create_user(
            email="rival@example.com",
            password="StrongPassword123!",
            name="Rival",
            accepted_terms=True,
        )
        self.rival_workspace = Workspace.objects.create(
            user=self.rival,
            business_name="Rival Studio",
        )
        self.rival_event = Event.objects.create(
            workspace=self.rival_workspace,
            title="Rival Event",
            slug="rival-event",
        )
        self.rival_scene = Scene.objects.create(
            event=self.rival_event,
            title="Rival Scene",
        )
        self.rival_photo = Photo.objects.create(
            scene=self.rival_scene,
            original_filename="rival.jpg",
            file_size_bytes=2048,
            status="READY",
            is_processed=True,
            r2_object_key="fast-lane/tenant_rival/photo-rival/rival.jpg",
        )

        self.client.force_authenticate(self.owner)

    def test_event_detail_is_scoped_to_authenticated_workspace(self):
        response = self.client.get(
            reverse("gallery:event-detail", args=[self.rival_event.id])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_event_update_is_scoped_to_authenticated_workspace(self):
        response = self.client.patch(
            reverse("gallery:event-detail", args=[self.rival_event.id]),
            {"title": "Hijacked"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.rival_event.refresh_from_db()
        self.assertEqual(self.rival_event.title, "Rival Event")

    def test_event_list_returns_only_owned_records(self):
        response = self.client.get(reverse("gallery:event-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(str(self.owner_event.id), returned_ids)
        self.assertNotIn(str(self.rival_event.id), returned_ids)

    def test_scene_detail_is_scoped_to_authenticated_workspace(self):
        response = self.client.get(
            reverse("gallery:scene-detail", args=[self.rival_scene.id])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_scene_create_rejects_cross_tenant_event_attachment(self):
        response = self.client.post(
            reverse("gallery:scene-list"),
            {"event": str(self.rival_event.id), "title": "Intrusion"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            Scene.objects.filter(event=self.rival_event, title="Intrusion").exists()
        )

    def test_scene_list_returns_only_owned_records(self):
        response = self.client.get(reverse("gallery:scene-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item["id"] for item in response.data}
        self.assertIn(str(self.owner_scene.id), returned_ids)
        self.assertNotIn(str(self.rival_scene.id), returned_ids)

    def test_photo_detail_is_scoped_to_authenticated_workspace(self):
        response = self.client.get(
            reverse("gallery:fastlane-photo-detail", args=[self.rival_photo.id])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_photo_delete_is_scoped_to_authenticated_workspace(self):
        response = self.client.delete(
            reverse("gallery:fastlane-photo-detail", args=[self.rival_photo.id])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Photo.objects.filter(id=self.rival_photo.id).exists())

