"""
Tests for the Django admin UI.

Architecture note: models were migrated from core.Gallery/core.Image
to gallery.Event/gallery.Scene/gallery.Photo. All admin registrations
and test fixtures now reflect the current model hierarchy:

    core.Workspace → gallery.Event → gallery.Scene → gallery.Photo
"""
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.models import Workspace
from gallery.models import Event, Photo, Scene

User = get_user_model()


class AdminSiteTests(TestCase):
    """Tests for Django admin — covers user, workspace, event, and photo pages."""

    def setUp(self):
        """
        Build the full object graph the admin pages depend on.
        Hierarchy: Workspace → Event → Scene → Photo
        """
        self.client = Client()

        self.admin_user = User.objects.create_superuser(
            email='admin@photobox.com',
            password='testpass123',
        )
        self.client.force_login(self.admin_user)

        self.user = User.objects.create_user(
            email='photographer@example.com',
            password='testpass123',
            name='Test Photographer',
        )

        self.workspace = Workspace.objects.create(
            user=self.user,
            business_name='Test Studios',
        )

        # gallery.Event (formerly core.Gallery)
        self.event = Event.objects.create(
            workspace=self.workspace,
            title='Admin Test Event',
            slug='admin-test-event',
        )

        # gallery.Scene (intermediate layer between Event and Photo)
        self.scene = Scene.objects.create(
            event=self.event,
            title='Admin Test Scene',
        )

        # gallery.Photo (formerly core.Image)
        self.photo = Photo.objects.create(
            scene=self.scene,
            original_filename='admin_test.jpg',
            file_size_bytes=1024,
        )

    # -----------------------------------------------------------------------
    # User admin
    # -----------------------------------------------------------------------

    def test_users_lists(self):
        """User list page renders and contains the test user's details."""
        url = reverse('admin:core_user_changelist')
        res = self.client.get(url)

        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.user.name)
        self.assertContains(res, self.user.email)

    def test_edit_user_page(self):
        """User change page loads for an existing user."""
        url = reverse('admin:core_user_change', args=[self.user.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    def test_create_user_page(self):
        """User add page loads without error."""
        url = reverse('admin:core_user_add')
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)

    # -----------------------------------------------------------------------
    # Workspace admin
    # -----------------------------------------------------------------------

    def test_workspace_admin_pages(self):
        """Workspace list and change pages load and display the test workspace."""
        list_url = reverse('admin:core_workspace_changelist')
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(
            res_list, self.workspace.business_name,
            msg_prefix="Workspace list page must display business_name.",
        )

        edit_url = reverse('admin:core_workspace_change', args=[self.workspace.id])
        res_edit = self.client.get(edit_url)
        self.assertEqual(res_edit.status_code, 200)

    # -----------------------------------------------------------------------
    # Event admin (formerly Gallery)
    # -----------------------------------------------------------------------

    def test_gallery_admin_pages(self):
        """
        Event list and change pages load with Scene inlines attached.
        (Model was renamed from core.Gallery to gallery.Event.)
        """
        list_url = reverse('admin:gallery_event_changelist')
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(
            res_list, self.event.title,
            msg_prefix="Event list page must display the event title.",
        )

        # Uses self.event.id — the Event's own PK, not the old gallery.id.
        edit_url = reverse('admin:gallery_event_change', args=[self.event.id])
        res_edit = self.client.get(edit_url)
        self.assertEqual(
            res_edit.status_code, 200,
            "Event change page returned a redirect (302). "
            "Check that the Event is registered in gallery/admin.py and "
            "that self.event.id matches a gallery.Event row — not a core.Gallery row.",
        )

    # -----------------------------------------------------------------------
    # Photo admin (formerly Image)
    # -----------------------------------------------------------------------

    def test_image_admin_pages(self):
        """
        Photo list and change pages load correctly.
        (Model was renamed from core.Image to gallery.Photo.)
        """
        list_url = reverse('admin:gallery_photo_changelist')
        res_list = self.client.get(list_url)
        self.assertEqual(res_list.status_code, 200)
        self.assertContains(
            res_list, self.photo.original_filename,
            msg_prefix="Photo list page must display the original filename.",
        )

        # Uses self.photo.id — the Photo's own PK, not the old image.id.
        edit_url = reverse('admin:gallery_photo_change', args=[self.photo.id])
        res_edit = self.client.get(edit_url)
        self.assertEqual(
            res_edit.status_code, 200,
            "Photo change page returned a redirect (302). "
            "Check that Photo is registered in gallery/admin.py and "
            "that self.photo.id matches a gallery.Photo row — not a core.Image row.",
        )
