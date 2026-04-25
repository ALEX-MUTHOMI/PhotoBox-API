from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from billing.models import BillingAuditLog, SubscriptionTier


User = get_user_model()


class BillingAuditLogImmutabilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="billing@example.com",
            password="StrongPassword123!",
            name="Billing User",
            accepted_terms=True,
        )

    def test_audit_log_can_be_created(self):
        entry = BillingAuditLog.objects.create(
            user=self.user,
            old_state=SubscriptionTier.FREE,
            new_state=SubscriptionTier.PRO,
            webhook_event_id="evt_123",
        )

        self.assertIsNotNone(entry.pk)

    def test_instance_delete_is_blocked(self):
        entry = BillingAuditLog.objects.create(
            user=self.user,
            old_state=SubscriptionTier.FREE,
            new_state=SubscriptionTier.PRO,
        )

        with self.assertRaises(PermissionDenied):
            entry.delete()

    def test_queryset_delete_is_blocked(self):
        BillingAuditLog.objects.create(
            user=self.user,
            old_state=SubscriptionTier.FREE,
            new_state=SubscriptionTier.PRO,
        )

        with self.assertRaises(PermissionDenied):
            BillingAuditLog.objects.filter(user=self.user).delete()

    def test_instance_update_is_blocked(self):
        entry = BillingAuditLog.objects.create(
            user=self.user,
            old_state=SubscriptionTier.FREE,
            new_state=SubscriptionTier.PRO,
        )
        entry.new_state = SubscriptionTier.FREE

        with self.assertRaises(PermissionDenied):
            entry.save()

    def test_queryset_update_is_blocked(self):
        BillingAuditLog.objects.create(
            user=self.user,
            old_state=SubscriptionTier.FREE,
            new_state=SubscriptionTier.PRO,
        )

        with self.assertRaises(PermissionDenied):
            BillingAuditLog.objects.filter(user=self.user).update(
                new_state=SubscriptionTier.FREE
            )

