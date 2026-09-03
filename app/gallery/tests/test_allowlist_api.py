"""A8: photographer CRUD for ClientAllowlist."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from core.models import Workspace
from gallery.models import ClientAllowlist, Event

User = get_user_model()


class ClientAllowlistApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="owner@example.com",
            password="StrongPassword123!",
            name="Owner",
            accepted_terms=True,
        )
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name="Owner Studio",
        )
        self.event = Event.objects.create(
            workspace=self.workspace,
            title="Wedding",
            slug="allowlist-wedding",
            is_published=True,
        )
        self.list_url = reverse("gallery:event-allowlist", args=[self.event.id])
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(self.user)}"
        )

    def test_unauthenticated_returns_401(self):
        self.client.credentials()
        res = self.client.get(self.list_url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_client_jwt_returns_403(self):
        self.user.gallery_id = str(self.event.id)
        self.client.force_authenticate(user=self.user)
        res = self.client.get(self.list_url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_normalizes_email_and_lists(self):
        res = self.client.post(self.list_url, {"email": "Bride@Example.com"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["email"], "bride@example.com")

        listed = self.client.get(self.list_url)
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertEqual(len(listed.data), 1)
        self.assertEqual(listed.data[0]["email"], "bride@example.com")

    def test_duplicate_email_returns_400(self):
        ClientAllowlist.objects.create(gallery=self.event, email="bride@example.com")
        res = self.client.post(self.list_url, {"email": "BRIDE@example.com"}, format="json")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cross_tenant_event_returns_404(self):
        rival = User.objects.create_user(
            email="rival@example.com",
            password="StrongPassword123!",
            name="Rival",
            accepted_terms=True,
        )
        rival_ws = Workspace.objects.create(user=rival, business_name="Rival Studio")
        rival_event = Event.objects.create(
            workspace=rival_ws,
            title="Rival Wedding",
            slug="rival-wedding",
        )
        url = reverse("gallery:event-allowlist", args=[rival_event.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        create_res = self.client.post(url, {"email": "x@example.com"}, format="json")
        self.assertEqual(create_res.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_removes_allowlist_entry(self):
        entry = ClientAllowlist.objects.create(
            gallery=self.event,
            email="bride@example.com",
        )
        detail_url = reverse(
            "gallery:event-allowlist-detail",
            args=[self.event.id, entry.pk],
        )
        res = self.client.delete(detail_url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            ClientAllowlist.objects.filter(gallery=self.event, email="bride@example.com").exists()
        )

    def test_delete_cross_tenant_entry_returns_404(self):
        rival = User.objects.create_user(
            email="rival2@example.com",
            password="StrongPassword123!",
            name="Rival2",
            accepted_terms=True,
        )
        rival_ws = Workspace.objects.create(user=rival, business_name="Rival 2")
        rival_event = Event.objects.create(
            workspace=rival_ws,
            title="Other",
            slug="other-wedding",
        )
        entry = ClientAllowlist.objects.create(
            gallery=rival_event,
            email="secret@example.com",
        )
        url = reverse(
            "gallery:event-allowlist-detail",
            args=[rival_event.id, entry.pk],
        )
        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(ClientAllowlist.objects.filter(pk=entry.pk).exists())
