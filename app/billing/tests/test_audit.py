from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from billing.models import BillingAuditLog

User = get_user_model()

class InsiderThreatSecurityTests(TransactionTestCase):
    """Penetration tests against rogue admins and database tampering."""

    def setUp(self):
        self.user = User.objects.create_user(email="client@test.com", password="password")
        self.subscription = self.user.subscription

        # We manually seed an audit log to test our defenses against it
        self.log = BillingAuditLog.objects.create(
            user=self.user,
            old_state="FREE",
            new_state="PRO"
        )

    def test_audit_logs_block_all_deletion_vectors(self):
        """
        HACKER/ROGUE ADMIN: Tries to cover tracks by deleting logs.
        DEFENSE: Block single delete AND bulk delete at the ORM QuerySet level.
        """
        # 1. Test Single Instance Delete
        # ENGINEER FIX: We specifically demand a PermissionDenied error, not a generic Exception
        with self.assertRaises(PermissionDenied):
            self.log.delete()

        # 2. Test Bulk QuerySet Delete (The typical bypass used by rogue scripts)
        with self.assertRaises(PermissionDenied):
            BillingAuditLog.objects.all().delete()

        # Verify data absolutely still exists
        self.assertEqual(BillingAuditLog.objects.count(), 1)

    def test_audit_logs_block_all_update_vectors(self):
        """
        HACKER/ROGUE ADMIN: Tries to alter history by rewriting existing logs.
        DEFENSE: Block instance updates AND bulk updates.
        """
        # 1. Test Single Instance Update
        self.log.new_state = "HACKED"
        with self.assertRaises(PermissionDenied):
            self.log.save()

        # 2. Test Bulk QuerySet Update (The silent killer)
        with self.assertRaises(PermissionDenied):
            BillingAuditLog.objects.all().update(new_state="HACKED")

        # Verify data was mathematically untouched
        self.log.refresh_from_db()
        self.assertEqual(self.log.new_state, "PRO")
