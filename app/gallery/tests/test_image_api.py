"""
Enterprise-Grade Tests for the PhotoBox Image API.
"""
import io
from PIL import Image as PILImage

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
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
    """Generates a secure, temporary binary image file purely in RAM."""
    file_obj = io.BytesIO()
    image = PILImage.new('RGB', size=(100, 100), color=(255, 0, 0))
    image.save(file_obj, 'JPEG')
    file_obj.seek(0)
    return SimpleUploadedFile('test_image.jpg', file_obj.read(), content_type='image/jpeg')


class ImageUploadApiTests(TestCase):
    """Production-ready test suite for the Image API."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_user(email='pro@example.com', password='testpass123')

        # Give the test user a strict 5GB storage limit to test the Quota Shield
        self.user.storage_limit_gb = 5
        self.user.save()

        self.workspace = Workspace.objects.create(user=self.user, business_name='Pro Studio')
        self.gallery = Gallery.objects.create(workspace=self.workspace, title='Summer Wedding', slug='summer-wedding')

        self.client.force_authenticate(self.user)

    # ==========================================
    # 1. THE HAPPY PATH & DATA INTEGRITY
    # ==========================================

    def test_upload_image_successful(self):
        """Test uploading a valid image file returns 201 Created and saves bytes."""
        payload = {
            'gallery': self.gallery.id,
            'title': 'Bride Portrait',
            'order': 1,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('image', res.data)

        # Verify the database correctly logged the file size for Quota math
        image_obj = Image.objects.get(id=res.data['id'])
        self.assertTrue(image_obj.file_size_bytes > 0)

    def test_filter_images_by_gallery(self):
        """REACT OPTIMIZATION: Proves the API can filter images so the UI only loads one gallery."""
        gallery2 = Gallery.objects.create(workspace=self.workspace, title='Winter', slug='winter')

        Image.objects.create(gallery=self.gallery)
        Image.objects.create(gallery=gallery2)

        res = self.client.get(IMAGES_URL, {'gallery': self.gallery.id})

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)

    def test_gallery_soft_delete_hides_images(self):
        """THE CASCADE: Proves that putting a gallery in the trash hides its images."""
        Image.objects.create(gallery=self.gallery)

        self.gallery.is_deleted = True
        self.gallery.save()

        res = self.client.get(IMAGES_URL)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 0)

    # ==========================================
    # 2. RED TEAM SCRIPTS (THE VAULT DEFENSES)
    # ==========================================

    def test_upload_missing_file_returns_400(self):
        """ERROR HANDLING: Proves the API cleanly rejects requests with no file attached."""
        payload = {'gallery': self.gallery.id, 'title': 'Ghost Image'}
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('image', res.data)

    def test_upload_to_unowned_gallery_blocked(self):
        """SECURITY (Tenant Isolation): Proves users cannot inject images into another client's workspace."""
        rival = create_user(email='hacker@example.com', password='password123')
        rival_workspace = Workspace.objects.create(user=rival, business_name='Rival')
        rival_gallery = Gallery.objects.create(workspace=rival_workspace, title='Rival', slug='rival')

        payload = {
            'gallery': rival_gallery.id,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_ghost_injection_shield(self):
        """SECURITY: Ensure hackers cannot upload files to a deleted gallery."""
        self.gallery.is_deleted = True
        self.gallery.save()

        payload = {
            'gallery': self.gallery.id,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_malware_mime_spoofing_shield(self):
        """SECURITY: Ensure disguised executable scripts are violently rejected by Pillow."""
        # Hacker renames a malicious script to .jpg to bypass basic frontend validation
        malicious_file = SimpleUploadedFile(
            'shell.jpg',
            b'import os; os.system("rm -rf /")',
            content_type='image/jpeg'
        )

        payload = {
            'gallery': self.gallery.id,
            'image': malicious_file
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_payload_too_large_shield(self):
        """SECURITY: Ensure files over 25MB are dropped before expensive processing."""
        # Create a dummy payload of exactly 26 Megabytes of zeroes
        massive_file = SimpleUploadedFile('massive.jpg', b'0' * 26 * 1024 * 1024, content_type='image/jpeg')

        payload = {
            'gallery': self.gallery.id,
            'image': massive_file
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_denial_of_wallet_quota_shield(self):
        """SECURITY (Billing): Ensure uploads are blocked if the user exceeds their 5GB SaaS plan."""
        # Mathematically simulate the user having exactly 5 Gigabytes of data
        five_gigabytes = 5 * 1024 * 1024 * 1024
        Image.objects.create(gallery=self.gallery, file_size_bytes=five_gigabytes)

        # They try to upload one more small file
        payload = {
            'gallery': self.gallery.id,
            'image': generate_test_image()
        }
        res = self.client.post(IMAGES_URL, payload, format='multipart')

        # The API must sum the database, realize they are at the limit, and reject the request
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('Quota Exceeded', str(res.data))
