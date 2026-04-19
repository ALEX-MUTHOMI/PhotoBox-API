import uuid
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator

class PricingPlan(models.Model):
    """
    Defines the available subscription tiers.
    Decouples pricing logic from the codebase, allowing Admin-level control.
    """
    name = models.CharField(max_length=100)
    lemon_squeezy_variant_id = models.CharField(max_length=100, unique=True)

    # BUSINESS LOGIC: The Graveyard Defense.
    # Set to False to stop new checkouts without breaking existing subscriptions.
    is_active = models.BooleanField(default=True, help_text="Uncheck to retire this plan.")

    # SECURITY: Price can never be negative
    price_usd = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        validators=[MinValueValidator(0.00)]
    )

    # SECURITY: Bandwidth can never be negative
    bandwidth_limit_bytes = models.BigIntegerField(
        validators=[MinValueValidator(0)],
        help_text="Monthly data cap in bytes"
    )

    # SECURITY: A gallery must exist for at least 1 day
    gallery_expiry_days = models.IntegerField(
        validators=[MinValueValidator(1)],
        help_text="Days until galleries expire (e.g., 30)"
    )

    # SECURITY: Commission must be between 0% and 100%
    commission_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.0,
        validators=[MinValueValidator(0.00), MaxValueValidator(100.00)]
    )

    features = models.JSONField(default=list, help_text="List of features for frontend display")

    def __str__(self):
        status = "ACTIVE" if self.is_active else "RETIRED"
        return f"{self.name} (${self.price_usd}) - [{status}]"


class CheckoutSession(models.Model):
    """
    Records a user's intent to purchase a specific plan.
    Provides the anchor for webhook fulfillment and tracks abandoned carts.
    """
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('ABANDONED', 'Abandoned'),
    )

    # COMPLIANCE UPDATE: SET_NULL preserves financial intent history for audits
    # and fraud forensics even if the user completely deletes their account.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checkout_sessions'
    )

    # If an Admin tries to delete a pricing plan that people have already purchased,
    # the database will throw a ProtectedError to prevent data corruption.
    plan = models.ForeignKey(PricingPlan, on_delete=models.PROTECT)

    # Cryptographically secure intent token injected into the Lemon Squeezy payload
    session_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')

    # Tracking timestamps for abandoned cart analytics
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # PERFORMANCE: When the webhook hits, it will search by session_token.
        # This index ensures the database finds the session in milliseconds.
        indexes = [
            models.Index(fields=['session_token']),
        ]

    def __str__(self):
        user_email = self.user.email if self.user else "DELETED_USER"
        return f"{user_email} - {self.plan.name} - {self.status}"
