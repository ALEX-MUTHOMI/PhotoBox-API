"""
Enterprise-Grade Tests for the Pixieset Standard Event & Scene APIs.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Workspace
from gallery.models import Event, Scene

EVENTS_URL = reverse('gallery:event-list')
SCENES_URL = reverse('gallery:scene-list')

def create_user(**params):
    return get_user_model().objects.create_user(**params)


class EventApiTests(TestCase):
    """Production-ready test suite for the Event API."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_user(email='pro@example.com', password='testpass123')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Pro Studio')
        self.client.force_authenticate(self.user)

    def test_create_event_successful(self):
        """Test creating a valid Event returns 201 Created and auto-generatesslug."""
        payload = {
            'title': 'The Smith Wedding',
            'event_type': 'WEDDING',
            'gallery_pin': '123456'
        }
        res = self.client.post(EVENTS_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('id', res.data)
        
        # Verify slug idempotency (crypto slug auto-generated)
        self.assertTrue(res.data['slug'].startswith('the-smith-wedding-'))
        
        # Verify PIN never leaks in response
        self.assertNotIn('gallery_pin', res.data)
        self.assertNotIn('_hashed_pin', res.data)
        
        # Verify actual database hashing
        event = Event.objects.get(id=res.data['id'])
        self.assertTrue(event.check_pin('123456'))

    def test_patch_empty_gallery_pin_clears_pin(self):
        """Photographers can remove a gallery PIN by sending an empty string."""
        event = Event.objects.create(
            workspace=self.workspace,
            title='PIN Event',
            slug='pin-event',
        )
        event.set_pin('123456')
        detail_url = reverse('gallery:event-detail', kwargs={'pk': event.id})

        res = self.client.patch(detail_url, {'gallery_pin': ''}, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertNotIn('gallery_pin', res.data)
        event.refresh_from_db()
        self.assertEqual(event._hashed_pin, '')
        self.assertFalse(event.check_pin('123456'))

    def test_patch_without_gallery_pin_leaves_pin_unchanged(self):
        event = Event.objects.create(
            workspace=self.workspace,
            title='Keep PIN',
            slug='keep-pin',
        )
        event.set_pin('567890')
        detail_url = reverse('gallery:event-detail', kwargs={'pk': event.id})

        res = self.client.patch(detail_url, {'title': 'Renamed'}, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        event.refresh_from_db()
        self.assertTrue(event.check_pin('567890'))

    def test_create_event_slug_is_safe_for_scriptable_title(self):
        """SECURITY: Generated slugs must not carry scriptable title content."""
        payload = {
            'title': '<script>alert(1)</script>',
            'event_type': 'OTHER',
        }
        res = self.client.post(EVENTS_URL, payload)

        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('<', res.data['slug'])
        self.assertNotIn('>', res.data['slug'])
        self.assertRegex(res.data['slug'], r'^[a-z0-9-]+-[a-f0-9]{8}$')

    def test_create_event_without_workspace_fails(self):
        """SECURITY: Verify missing workspace cleanly raises 400 Bad Request."""
        hacker = create_user(email='hacker@example.com', password='pwd')
        hacker_client = APIClient()
        hacker_client.force_authenticate(hacker)
        
        # Hacker has no workspace initialized
        res = hacker_client.post(EVENTS_URL, {'title': 'Ghost Event'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_event_tenant_isolation(self):
        """SECURITY: Users can only retrieve their own events."""
        rival = create_user(email='rival@example.com', password='pwd')
        rival_ws = Workspace.objects.create(user=rival, business_name='Rival Studio')
        Event.objects.create(workspace=rival_ws, title='Rival Event', slug='rival')
        
        my_event = Event.objects.create(workspace=self.workspace, title='My Event', slug='my-event')
        
        res = self.client.get(EVENTS_URL)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['id'], str(my_event.id))


class SceneApiTests(TestCase):
    """Production-ready test suite for the Scene (Tabs) API."""

    def setUp(self):
        self.client = APIClient()
        self.user = create_user(email='pro@example.com', password='testpass123')
        self.workspace = Workspace.objects.create(user=self.user, business_name='Pro Studio')
        self.event = Event.objects.create(workspace=self.workspace, title='Wedding', slug='wedding')
        self.client.force_authenticate(self.user)
        
    def test_create_scene_successful(self):
        """Test creating a sub-category under an Event."""
        payload = {
            'event': self.event.id,
            'title': 'Ceremony',
            'display_order': 1
        }
        res = self.client.post(SCENES_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Scene.objects.count(), 1)

    def test_create_scene_cross_tenant_hijack_fails(self):
        """SECURITY: Cannot attach a scene to another user's event."""
        rival = create_user(email='rival@example.com', password='pwd')
        rival_ws = Workspace.objects.create(user=rival, business_name='Rival Studio')
        rival_event = Event.objects.create(workspace=rival_ws, title='Rival Event', slug='rival')
        
        payload = {
            'event': rival_event.id,
            'title': 'Hacked Scene',
        }
        res = self.client.post(SCENES_URL, payload)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
