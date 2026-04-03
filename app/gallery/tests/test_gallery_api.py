"""
Tests for the Gallery API.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Gallery, Workspace

GALLERIES_URL = reverse('gallery:gallery-list')

def detail_url(gallery_id):
    """Create and return a gallery detail URL."""
    return reverse('gallery:gallery-detail', args=[gallery_id])

def create_user(**params):
    return get_user_model().objects.create_user(**params)

class PrivateGalleryApiTests(TestCase):
    """Test authenticated API requests."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_user(email='photographer@example.com', password='testpass123')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Apex Photo')
        self.client.force_authenticate(self.user)

    def test_retrieve_galleries_ignores_deleted(self):
        """Test retrieving galleries only returns active, non-deleted ones."""
        # Create one active gallery
        Gallery.objects.create(workspace=self.workspace, title='Active', slug='active')
        # Create one soft-deleted gallery
        Gallery.objects.create(workspace=self.workspace, title='Trashed', slug='trashed', is_deleted=True)

        res = self.client.get(GALLERIES_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1) # Proves the trash can works!
        self.assertEqual(res.data[0]['title'], 'Active')

    def test_create_gallery_with_pin_is_hashed(self):
        """SECURITY: Test creating a gallery with a PIN automatically hashes it."""
        payload = {
            'title': 'Secret Wedding',
            'slug': 'secret-wedding',
            'is_public': False,
            'gallery_pin': '2026'
        }
        res = self.client.post(GALLERIES_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        gallery = Gallery.objects.get(id=res.data['id'])
        # Assert the pin is NOT stored as '2026'
        self.assertNotEqual(gallery.gallery_pin, '2026')
        self.assertTrue(gallery.verify_pin('2026'))

        # Assert the API response does NOT leak the pin
        self.assertNotIn('gallery_pin', res.data)

    def test_delete_gallery_is_soft_delete(self):
        """Test that deleting via the API only soft-deletes the gallery."""
        gallery = Gallery.objects.create(workspace=self.workspace, title='To Delete', slug='delete')

        url = detail_url(gallery.id)
        res = self.client.delete(url)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

        # Retrieve the gallery directly from the database to prove it still exists
        gallery.refresh_from_db()
        self.assertTrue(gallery.is_deleted)
