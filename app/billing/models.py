"""Billing models: subscriptions, webhook idempotency, and immutable audit logs."""

from django.db import models
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.exceptions import PermissionDenied

# ==========================================
# MODULE 1 & 2: THE LEDGERS
# ==========================================
class RegistrationLog(models.Model):
    email = models.EmailField()
    ip_hash = models.CharField(max_length=64, db_index=True)
    user_agent = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Reg: {self.email} (Hash: {self.ip_hash[:8]})"

class ProcessedWebhook(models.Model):
    event_id = models.CharField(max_length=150, unique=True, db_index=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.event_id

# ==========================================
# MODULE 3: THE QUOTA VAULT (SQL HARDENED)
# ==========================================
class Subscription(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription')
    is_pro = models.BooleanField(default=False)
    storage_limit_bytes = models.BigIntegerField(default=1073741824) # 1GB
    storage_used_bytes = models.BigIntegerField(default=0)

    # Indexed for faster Lemon Squeezy webhook lookups
    lemon_squeezy_customer_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)
    lemon_squeezy_subscription_id = models.CharField(max_length=100, blank=True, null=True, db_index=True)

    class Meta:
        constraints = [
            # SECURITY: Mathematically prevents the "Negative Quota" hack at the SQL database level
            models.CheckConstraint(
                condition=models.Q(storage_used_bytes__gte=0),
                name='prevent_negative_storage_used'
            ),
            models.CheckConstraint(
                condition=models.Q(storage_limit_bytes__gte=0),
                name='prevent_negative_storage_limit'
            )
        ]

    def __str__(self):
        plan = "PRO" if self.is_pro else "FREE"
        # Removed self.user.email to prevent N+1 query crashes in Django Admin
        return f"User ID {self.user_id} - {plan}"

# ==========================================
# MODULE 4: THE OBSERVABILITY LEDGER
# ==========================================
class SubscriptionTier(models.TextChoices):
    """Enforces strict financial state naming conventions."""
    FREE = 'FREE', 'Free Tier'
    PRO = 'PRO', 'Pro Tier'

class ImmutableQuerySet(models.QuerySet):
    def delete(self):
        raise PermissionDenied("Audit logs are immutable. Bulk deletion blocked.")
    def update(self, **kwargs):
        raise PermissionDenied("Audit logs are immutable. Bulk updates blocked.")

class ImmutableManager(models.Manager):
    def get_queryset(self):
        return ImmutableQuerySet(self.model, using=self._db)

class BillingAuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    old_state = models.CharField(max_length=10, choices=SubscriptionTier.choices)
    new_state = models.CharField(max_length=10, choices=SubscriptionTier.choices)
    webhook_event_id = models.CharField(max_length=150, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    objects = ImmutableManager()

    class Meta:
        # High-performance indexing for financial audits
        indexes = [
            models.Index(fields=['user', '-timestamp']),
        ]

    def __str__(self):
        return f"User ID {self.user_id} | {self.old_state} -> {self.new_state}"

    def delete(self, *args, **kwargs):
        raise PermissionDenied("Audit logs are immutable. Instance deletion blocked.")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise PermissionDenied("Audit logs are immutable. Instance updates blocked.")
        super().save(*args, **kwargs)

# ==========================================
# MODULE 5: THE FAILSAFE (DLQ)
# ==========================================
class DeadLetterQueue(models.Model):
    """Stores failed webhook payloads for manual recovery. Prevents Data Loss."""
    event_id = models.CharField(max_length=150, unique=True)
    payload = models.JSONField()
    error_message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"DLQ: {self.event_id}"


class DarajaCallbackToken(models.Model):
    """
    Unguessable single-use STK callback secret (R2.6).

    Daraja callbacks are not HMAC-signed like Lemon Squeezy; we mint a hashed
    secret_token at STK initiate and require it on the callback URL.
    """
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daraja_callback_tokens",
    )
    checkout_request_id = models.CharField(max_length=64, blank=True, default="")
    expires_at = models.DateTimeField(db_index=True)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Daraja token {self.token_hash[:8]}…"


# ==========================================
# DATABASE AUTOMATION (SIGNALS)
# ==========================================
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_subscription(sender, instance, created, **kwargs):
    """Automatically provisions the 1GB Quota Vault upon registration."""
    if created:
        Subscription.objects.create(user=instance)
