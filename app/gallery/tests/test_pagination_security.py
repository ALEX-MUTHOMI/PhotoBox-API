from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from gallery.models import Workspace, Event, Scene, Photo

User = get_user_model()

class PaginationSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email='paginator@test.com', password='password123')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Pagination Studio')
        self.event = Event.objects.create(workspace=self.workspace, title='Big Event', slug='big-event')
        self.scene = Scene.objects.create(event=self.event, title='Main Scene')
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        Photo.objects.bulk_create([
            Photo(scene=self.scene, r2_object_key=f'raw/tenant_1/scene_1/photo_{i:04d}.jpg', file_size_bytes=1024)
            for i in range(150)
        ])

    def test_photo_list_is_paginated(self):
        res = self.client.get(reverse('gallery:fastlane-photo-list'))# Adjust url name if needed
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsInstance(res.data, (dict, list))
        for key in ('results', 'count', 'next', 'previous'):
            self.assertIn(key, res.data)
        self.assertLessEqual(len((res.data.get('results', res.data) if isinstance(res.data, dict) else res.data)), 100)
        self.assertEqual(res.data['count'], 150)

    def test_page_size_cap_prevents_dos(self):
        res = self.client.get(reverse('gallery:fastlane-photo-list'), {'page_size': 999999})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len((res.data.get('results', res.data) if isinstance(res.data, dict) else res.data)), 100)