"""
Tests for the Gallery API.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from rest_framework import status
from rest_framework.test import APIClient

from core.models import Gallery, Workspace

# The URL routing name we will build
GALLERIES_URL = reverse('gallery:gallery-list')


def create_user(**params):
    """Create and return a sample user."""
    return get_user_model().objects.create_user(**params)


class PublicGalleryApiTests(TestCase):
    """Test unauthenticated API requests."""

    def setUp(self):
        """Set up the test client."""
        self.client = APIClient()

    def test_auth_required(self):
        """Test that authentication is required to call the API."""
        res = self.client.get(GALLERIES_URL)

        # SECURITY GUARDRAIL: Proves anonymous users get bounced
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)


class PrivateGalleryApiTests(TestCase):
    """Test authenticated API requests."""

    def setUp(self):
        """Set up the test client and authenticate a user."""
        self.client = APIClient()
        self.user = create_user(
            email='photographer@example.com',
            password='testpass123'
        )
        # Create the Workspace for the user
        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name='Apex Photo'
        )

        # Authenticate the client (simulating a logged-in photographer)
        self.client.force_authenticate(self.user)

    def test_retrieve_galleries(self):
        """Test retrieving a list of galleries."""
        # Create two sample galleries inside the user's workspace
        Gallery.objects.create(
            workspace=self.workspace, title='Wedding 2026', slug='wedding-2026'
        )
        Gallery.objects.create(
            workspace=self.workspace, title='Studio Headshots', slug='studio-headshots'
        )

        res = self.client.get(GALLERIES_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # We expect 2 galleries to be returned
        self.assertEqual(len(res.data), 2)

    def test_galleries_list_limited_to_user_workspace(self):
        """Test that the list of galleries is limited to the authenticated user's workspace."""
        # 1. Create a rival photographer and their workspace
        rival_user = create_user(email='rival@example.com', password='password123')
        rival_workspace = Workspace.objects.create(
            user=rival_user, business_name='Rival Studio'
        )
        # 2. Give the rival a gallery
        Gallery.objects.create(
            workspace=rival_workspace, title='Rival Gallery', slug='rival-gallery'
        )

        # 3. Give OUR authenticated user a gallery
        Gallery.objects.create(
            workspace=self.workspace, title='Our Gallery', slug='our-gallery'
        )

        # 4. Make the request
        res = self.client.get(GALLERIES_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1) # Should only see OUR gallery, not the rival's
        self.assertEqual(res.data[0]['title'], 'Our Gallery')
