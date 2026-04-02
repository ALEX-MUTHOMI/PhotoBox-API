"""
Tests for PhotoBox database models.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from core import models


def create_user(email='photographer@example.com', password='testpass123'):
    """Create and return a new user."""
    return get_user_model().objects.create_user(email, password)


class ModelTests(TestCase):
    """Test models."""

    def test_create_user_with_email_successful(self):
        """Test creating a user with an email is successful."""
        email = 'test@example.com'
        password = 'testpass123'
        user = get_user_model().objects.create_user(
            email=email,
            password=password,
        )

        self.assertEqual(user.email, email)
        self.assertTrue(user.check_password(password))

    def test_new_user_email_normalized(self):
        """Test email is normalized for new users."""
        sample_emails = [
            ['test1@EXAMPLE.com', 'test1@example.com'],
            ['Test2@Example.com', 'Test2@example.com'],
            ['TEST3@EXAMPLE.com', 'TEST3@example.com'],
            ['test4@example.COM', 'test4@example.com'],
        ]
        for email, expected in sample_emails:
            user = get_user_model().objects.create_user(email, 'sample123')
            self.assertEqual(user.email, expected)

    def test_new_user_without_email_raises_error(self):
        """Test that creating a user without an email raises a ValueError."""
        with self.assertRaises(ValueError):
            get_user_model().objects.create_user('', 'test123')

    def test_create_superuser(self):
        """Test creating a superuser."""
        user = get_user_model().objects.create_superuser(
            'admin@photobox.com',
            'test123',
        )

        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)

    # --- NEW PHOTOBOX SAAS TESTS BELOW ---

    def test_user_has_saas_fields(self):
        """Test that our custom user model initializes with SaaS billing/storage fields."""
        user = create_user()

        # Photographers should start on a free tier with a 5GB limit
        self.assertEqual(user.stripe_customer_id, '')
        self.assertEqual(user.subscription_tier, 'free')
        self.assertEqual(user.storage_limit_gb, 5)

    def test_create_workspace(self):
        """Test creating a Workspace for a photographer is successful."""
        user = create_user()
        workspace = models.Workspace.objects.create(
            user=user,
            business_name='Apex Photography',
            custom_domain='gallery.apexphotography.com'
        )

        self.assertEqual(str(workspace), workspace.business_name)
        self.assertEqual(workspace.user, user)
