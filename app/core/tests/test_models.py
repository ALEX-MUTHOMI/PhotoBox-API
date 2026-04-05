"""
Tests for PhotoBox database models.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from core import models


def create_user(email='photographer@example.com', password='testpass123', **extra_fields):
    """Create and return a new user with secure hashing."""
    return get_user_model().objects.create_user(email, password, **extra_fields)


class ModelTests(TestCase):
    """Test core models."""

    def test_create_user_with_email_successful(self):
        user = create_user()
        self.assertEqual(user.email, 'photographer@example.com')
        self.assertTrue(user.check_password('testpass123'))
        self.assertEqual(user.subscription_tier, 'FREE')

    # =========================================================
    # NEW COMPLIANCE & SECURITY MODEL TESTS
    # =========================================================
    def test_user_accepted_terms_default_is_false(self):
        """
        SECURITY (Compliance): Ensure "Implicit Consent" is mathematically impossible.
        If a user is created via a backend script or admin panel without explicitly
        providing consent, the database MUST default to False.
        """
        user = create_user(email='nocheckbox@example.com')
        self.assertFalse(user.accepted_terms)

    def test_user_accepted_terms_saves_correctly(self):
        """
        SECURITY (Compliance): Ensure explicit consent is successfully saved
        to the database for permanent legal audit trails.
        """
        user = create_user(email='consented@example.com', accepted_terms=True)
        self.assertTrue(user.accepted_terms)
    # =========================================================

    def test_create_workspace_with_branding(self):
        """Test creating a workspace including frontend UI fields."""
        user = create_user()
        workspace = models.Workspace.objects.create(
            user=user,
            business_name='Apex Photography',
            brand_color='#FF5733'
        )
        self.assertEqual(workspace.brand_color, '#FF5733')
        self.assertFalse(workspace.is_deleted) # Proves SoftDeleteModel works

    def test_gallery_pin_is_hashed(self):
        """SECURITY: Test that the gallery PIN is mathematically hashed, not plaintext."""
        user = create_user()
        workspace = models.Workspace.objects.create(user=user, business_name='Apex')

        raw_pin = '2026'
        gallery = models.Gallery.objects.create(
            workspace=workspace,
            title='Wedding',
            slug='wedding',
            gallery_pin=raw_pin
        )

        # The stored pin MUST NOT be the raw pin
        self.assertNotEqual(gallery.gallery_pin, raw_pin)
        # UPDATE THIS LINE: The test now expects Argon2 (or legacy pbkdf2)
        self.assertTrue(gallery.gallery_pin.startswith(('pbkdf2_', 'argon2')))
        # The verify method must work
        self.assertTrue(gallery.verify_pin(raw_pin))

    def test_soft_delete_mechanic(self):
        """Test that objects can be softly deleted without destroying the database row."""
        user = create_user()
        workspace = models.Workspace.objects.create(user=user, business_name='Apex')
        gallery = models.Gallery.objects.create(workspace=workspace, title='Delete Me', slug='delete-me')

        # Simulate a delete
        gallery.is_deleted = True
        gallery.save()

        # The row still exists in the DB for data recovery, but is marked deleted
        recovered_gallery = models.Gallery.objects.get(id=gallery.id)
        self.assertTrue(recovered_gallery.is_deleted)














