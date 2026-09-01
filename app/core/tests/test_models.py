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
        self.assertEqual(user.storage_limit_gb, 1) # VERIFIES BILLING ENGINE ALIGNMENT

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

    def test_workspace_soft_delete_hidden_from_default_manager(self):
        user = create_user(email='soft@example.com')
        workspace = models.Workspace.objects.create(user=user, business_name='Soft Studio')
        workspace.is_deleted = True
        workspace.save(update_fields=['is_deleted'])

        with self.assertRaises(models.Workspace.DoesNotExist):
            models.Workspace.objects.get(id=workspace.id)

        recovered = models.Workspace.all_objects.get(id=workspace.id)
        self.assertTrue(recovered.is_deleted)
