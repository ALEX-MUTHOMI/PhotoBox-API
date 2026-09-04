"""OpenAPI / Spectacular contracts: generates, excludes Daraja, photographer-gated."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import Workspace


User = get_user_model()


class OpenAPISchemaTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.photographer = User.objects.create_user(
            email="pro@example.com",
            password="StrongPassword123!",
            name="Pro",
            accepted_terms=True,
        )
        Workspace.objects.create(user=self.photographer, business_name="Studio")
        self.token = str(RefreshToken.for_user(self.photographer).access_token)

    def test_schema_generates_openapi3_without_daraja(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.token}")
        res = self.client.get(reverse("api-schema"))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        body = res.content.decode("utf-8").lower()
        self.assertIn("openapi", body)
        self.assertNotIn("daraja", body)
        # Photographer field *names* like r2_object_key may appear; raw vault URLs must not.
        self.assertNotIn("https://", body)
        self.assertNotIn(".r2.cloudflarestorage.com", body)

    @override_settings(DEBUG=False, SECURE_SSL_REDIRECT=False)
    def test_anonymous_schema_rejected_when_not_public(self):
        res = self.client.get(reverse("api-schema"))
        self.assertIn(res.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_non_photographer_gallery_jwt_cannot_fetch_schema(self):
        # Photographer JWT without workspace is still IsPhotographerUser-ok if no gallery_id.
        # Simulate a principal that looks authenticated but carries gallery_id via force_authenticate
        # is not enough — IsPhotographerUser checks request.user.gallery_id.
        client_user = User.objects.create_user(
            email="clientish@example.com",
            password="StrongPassword123!",
            name="Clientish",
            accepted_terms=True,
        )
        client_user.gallery_id = "00000000-0000-0000-0000-000000000099"
        self.client.force_authenticate(user=client_user)
        res = self.client.get(reverse("api-schema"))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
