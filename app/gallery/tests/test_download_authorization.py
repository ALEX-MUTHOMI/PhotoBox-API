from unittest.mock import patch
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from gallery.models import Workspace, Event, Scene, Photo

User = get_user_model()

R2_SETTINGS = dict(
    CLOUDFLARE_R2_ENDPOINT='https://test.r2.cloudflarestorage.com',
    CLOUDFLARE_R2_BUCKET_NAME='test-bucket',
    CLOUDFLARE_R2_DOMAIN='test-domain.r2.dev',
    CLOUDFLARE_ACCESS_KEY_ID='test-key',
    CLOUDFLARE_SECRET_ACCESS_KEY='test-secret',
)

@override_settings(**R2_SETTINGS)
class PresignedGetUrlTenantIsolationTests(TestCase):
    def setUp(self):
        self.victim = User.objects.create_user(email='victim@studio.com', password='password123')
        self.victim_workspace = Workspace.objects.create(user=self.victim, business_name='Victim Studio')
        self.victim_event = Event.objects.create(workspace=self.victim_workspace, title='Private Wedding', slug='private-wedding')
        self.victim_scene = Scene.objects.create(event=self.victim_event, title='Ceremony')
        self.victim_photo = Photo.objects.create(
            scene=self.victim_scene,
            r2_object_key='raw/victim/kiss.jpg',
            file_size_bytes=5000,
            status='READY',
            is_processed=True,
        )

        self.attacker = User.objects.create_user(email='attacker@evil.com', password='password123')
        self.attacker_workspace = Workspace.objects.create(user=self.attacker, business_name='Attacker Co')

        self.attacker_client = APIClient()
        self.attacker_client.force_authenticate(user=self.attacker)
        self.victim_client = APIClient()
        self.victim_client.force_authenticate(user=self.victim)

    @patch('gallery.storage.generate_r2_presigned_get_url')
    def test_cross_tenant_download_url_hijack_blocked(self, mock_presign):
        mock_presign.return_value = 'https://r2/signed-url'
        download_url = reverse('gallery:fastlane-photo-download-url', kwargs={'pk': self.victim_photo.pk}) # Adjust url name if needed
        res = self.attacker_client.get(download_url)

        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        mock_presign.assert_not_called()

    @patch('gallery.storage.generate_r2_presigned_get_url')
    def test_owner_can_get_download_url(self, mock_presign):
        mock_presign.return_value = 'https://r2/signed-url'
        download_url = reverse('gallery:fastlane-photo-download-url', kwargs={'pk': self.victim_photo.pk})
        res = self.victim_client.get(download_url)

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res.data)
        mock_presign.assert_called_once()
