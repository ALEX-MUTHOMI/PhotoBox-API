"""
Enterprise-Grade Tests for the PhotoBox Image API.
"""
import tempfile
import os
from PIL import Image as PILImage

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Gallery, Workspace, Image

IMAGES_URL = reverse('gallery:image-list')

def image_detail_url(image_id):
    """Return image detail URL."""
    return reverse('gallery:image-detail', args=[image_id])

def create_user(**params):
    return get_user_model().objects.create_user(**params)

def generate_test_image():
    """Generates a secure, temporary binary image file in memory."""
    image = PILImage.new('RGB', (100, 100))
    tmp_file = tempfile.NamedTemporaryFile(suffix='.jpg')
    image.save(tmp_file, format='JPEG')
    tmp_file.seek(0)
    return tmp_file

class ImageUploadApiTests(TestCase):
    """Production-ready test suite for Image API."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_user(email='pro@example.com', password='testpass123')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Pro Studio')
        self.gallery = Gallery.objects.create(workspace=self.workspace, title='Summer Wedding', slug='summer-wedding')

        self.client.force_authenticate(self.user)

    # --- 1. THE HAPPY PATH ---

    def test_upload_image_successful(self):
        """Test uploading a valid image file returns 201 Created."""
        payload = {
            'gallery': self.gallery.id,
            'title': 'Bride Portrait',
            'order': 1,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('image', res.data)
        self.assertTrue(Image.objects.filter(id=res.data['id']).exists())

    # --- 2. ERROR HANDLING & LOGGING CATCHES ---

    def test_upload_missing_file_returns_400(self):
        """ERROR HANDLING: Proves the API cleanly rejects requests with no file attached."""
        payload = {
            'gallery': self.gallery.id,
            'title': 'Ghost Image'
            # Intentionally missing the 'image' file
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        # Assert the API specifically flags the 'image' field as the problem for the React UI
        self.assertIn('image', res.data)

    def test_upload_invalid_image_format_rejected(self):
        """SECURITY: Proves Pillow intercepts fake/malicious file signatures."""
        tmp_file = tempfile.NamedTemporaryFile(suffix='.jpg')
        tmp_file.write(b'import os; os.system("rm -rf /")') # A fake malicious script
        tmp_file.seek(0)

        payload = {
            'gallery': self.gallery.id,
            'title': 'Hacker Upload',
            'image': tmp_file
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # --- 3. TENANT ISOLATION (SECURITY) ---

    def test_upload_to_unowned_gallery_blocked(self):
        """SECURITY: Proves users cannot inject images into another client's workspace."""
        rival = create_user(email='hacker@example.com', password='password123')
        rival_workspace = Workspace.objects.create(user=rival, business_name='Rival')
        rival_gallery = Gallery.objects.create(workspace=rival_workspace, title='Rival', slug='rival')

        payload = {
            'gallery': rival_gallery.id,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- 4. DATA INTEGRITY & ARCHITECTURE ---

    def test_filter_images_by_gallery(self):
        """REACT OPTIMIZATION: Proves the API can filter images so the UI only loads one gallery at a time."""
        gallery2 = Gallery.objects.create(workspace=self.workspace, title='Winter Elopement', slug='winter')

        # Create an image in Gallery 1 and Gallery 2
        Image.objects.create(gallery=self.gallery)
        Image.objects.create(gallery=gallery2)

        # The React frontend will append ?gallery=ID to the URL
        res = self.client.get(IMAGES_URL, {'gallery': self.gallery.id})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1) # Should only return Gallery 1's image

    def test_gallery_soft_delete_hides_images(self):
        """THE CASCADE: Proves that putting a gallery in the trash hides its images from the API."""
        Image.objects.create(gallery=self.gallery)

        # The user soft-deletes the parent gallery
        self.gallery.is_deleted = True
        self.gallery.save()

        res = self.client.get(IMAGES_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # The image still exists in the DB, but the API refuses to serve it because the parent is trashed
        self.assertEqual(len(res.data), 0)
