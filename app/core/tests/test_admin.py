"""
Tests for the Django admin UI.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import Client

from core import models


class AdminSiteTests(TestCase):
    """Tests for Django admin."""

    def setUp(self):
        """Create admin user, test user, and dummy data for admin pages."""
        self.client = Client()
        self.admin_user = get_user_model().objects.create_superuser(
            email='admin@photobox.com',
            password='testpass123',
        )
        self.client.force_login(self.admin_user)

        self.user = get_user_model().objects.create_user(
            email='photographer@example.com',
            password='testpass123',
            name='Test Photographer'
        )

        # Create dummy resources to test the admin detail pages
        self.workspace = models.Workspace.objects.create(
            user=self.user,
            business_name='Test Studios'
        )
        self.gallery = models.Gallery.objects.create(
            workspace=self.workspace,
            title='Admin Test Gallery',
            slug='admin-test-gallery'
        )
        self.image = models.Image.objects.create(
            gallery=self.gallery,
            title='Admin Test Image'
        )

    # --- USER ADMIN TESTS ---

    def test_users_lists(self):
        """Test that users are listed on page."""
        url = reverse('admin:core_user_changelist')
        res = self.client.get(url)

        self.assertContains(res, self.user.name)
        self.assertContains(res, self.user.email)
        self.assertContains(res, self.user.subscription_tier)

    def test_edit_user_page(self):
        """Test the edit user page works."""
        url = reverse('admin:core_user_change', args=[self.user.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    def test_create_user_page(self):
        """Test the create user page works."""
        url = reverse('admin:core_user_add')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    # --- SAAS RESOURCE ADMIN TESTS ---

    def test_workspace_admin_pages(self):
        """Test that the Workspace list and edit pages load without crashing."""
        list_url = reverse('admin:core_workspace_changelist')
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(res_list, self.workspace.business_name)

        edit_url = reverse('admin:core_workspace_change', args=[self.workspace.id])
        res_edit = self.client.get(edit_url)
        self.assertEqual(res_edit.status_code, 200)

    def test_gallery_admin_pages(self):
        """Test that the Gallery list and edit pages load with inlines attached."""
        list_url = reverse('admin:core_gallery_changelist')
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)

        edit_url = reverse('admin:core_gallery_change', args=[self.gallery.id])
        res_edit = self.client.get(edit_url)
        self.assertEqual(res_edit.status_code, 200)

    def test_image_admin_pages(self):
        """Test that the Image list and edit pages load correctly."""
        list_url = reverse('admin:core_image_changelist')
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)

        edit_url = reverse('admin:core_image_change', args=[self.image.id])
        res_edit = self.client.get(edit_url)
        self.assertEqual(res_edit.status_code, 200)
