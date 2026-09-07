"""
Core Database Models for the PhotoBox SaaS API.
"""
import os
import uuid
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

# --- SECURE FILE PATH GENERATORS ---
def workspace_logo_file_path(instance, filename):
    ext = os.path.splitext(filename)[1]
    return os.path.join('uploads', 'workspace', 'logos', f'{uuid.uuid4()}{ext}')

def workspace_watermark_file_path(instance, filename):
    ext = os.path.splitext(filename)[1]
    return os.path.join('uploads', 'workspace', 'watermarks', f'{uuid.uuid4()}{ext}')


def workspace_image_file_path(instance, filename):
    """Legacy upload_to kept for historical migrations referencing core.Image."""
    ext = os.path.splitext(filename)[1]
    return os.path.join('uploads', 'workspace', 'legacy', f'{uuid.uuid4()}{ext}')


def validate_png_watermark(uploaded_file):
    if not uploaded_file:
        return

    filename = (getattr(uploaded_file, 'name', '') or '').lower()
    if not filename.endswith('.png'):
        raise ValidationError("Watermark logo must use the .png extension.")

    position = None
    if hasattr(uploaded_file, 'tell'):
        position = uploaded_file.tell()

    try:
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(0)
        with PILImage.open(uploaded_file) as image:
            if image.format != 'PNG':
                raise ValidationError("Watermark logo must be a valid PNG image.")
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValidationError("Watermark logo must be a valid PNG image.") from exc
    finally:
        if hasattr(uploaded_file, 'seek'):
            uploaded_file.seek(position or 0)


# ==========================================
# 1. ABSTRACT BASE MODELS (Audit & Data Retention)
# ==========================================
class SoftDeleteQuerySet(models.QuerySet):
    def alive(self):
        return self.filter(is_deleted=False)

    def dead(self):
        return self.filter(is_deleted=True)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):
    use_in_migrations = True

    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class SoftDeleteModel(models.Model):
    """Base model providing UUIDs, audit timestamps, and soft-delete capabilities."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    objects = SoftDeleteManager()
    all_objects = models.Manager()

    class Meta:
        abstract = True
        base_manager_name = 'all_objects'


# ==========================================
# 2. AUTHENTICATION & BILLING
# ==========================================
class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('User must have an email address.')
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password):
        user = self.create_user(email, password)
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    """Account owners (Photographers). Clients do NOT use this."""
    class SubscriptionTier(models.TextChoices):
        FREE = 'FREE', 'Free Tier'
        PRO = 'PRO', 'Professional'

    email = models.EmailField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    accepted_terms = models.BooleanField(default=False)
    tos_accepted_at = models.DateTimeField(blank=True, null=True)
    tos_version = models.CharField(max_length=64, blank=True)

    # Billing State
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    subscription_tier = models.CharField(max_length=20, choices=SubscriptionTier.choices, default=SubscriptionTier.FREE)

    # Storage limit tracked in GB for easy human/billing logic
    storage_limit_gb = models.IntegerField(default=1)

    objects = UserManager()
    USERNAME_FIELD = 'email'

    def save(self, *args, **kwargs):
        """
        Persist the photographer account.

        `storage_limit_gb` is display math only. The Workspace byte ledger is
        written by billing (Lemon Squeezy) and signup — not overwritten here.
        """
        super().save(*args, **kwargs)


# ==========================================
# 3. TENANT ISOLATION & FRONTEND BRANDING
# ==========================================
class Workspace(SoftDeleteModel):
    """The tenant boundary. Holds UX configuration and EDA Quotas."""

    # THE EDA FIX: Changed from ForeignKey to OneToOneField.
    # Guarantees 100% safety for Workspace.objects.get(user=user)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='workspace')

    business_name = models.CharField(max_length=255)
    custom_domain = models.CharField(max_length=255, blank=True, null=True, unique=True)

    # Frontend Branding
    logo = models.ImageField(upload_to=workspace_logo_file_path, null=True, blank=True)
    brand_color = models.CharField(max_length=7, default='#000000')
    watermark_logo = models.ImageField(
        upload_to=workspace_watermark_file_path,
        null=True,
        blank=True,
        validators=[validate_png_watermark],
    )
    watermark_opacity = models.PositiveSmallIntegerField(
        default=35,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    # --- EDA UPGRADE: The Atomic Quota Ledger ---
    # MinValueValidator enforces database-level integrity against negative storage hacks
    storage_limit_bytes = models.BigIntegerField(default=1 * 1024 * 1024 * 1024, validators=[MinValueValidator(0)])
    storage_used_bytes = models.BigIntegerField(default=0, validators=[MinValueValidator(0)])

    def __str__(self):
        return self.business_name

    def save(self, *args, **kwargs):
        previous_domain = None
        previous_deleted = False
        # UUID PKs are assigned before INSERT, so `self.pk` is not a reliable
        # "this row already exists" check. `_state.adding` is.
        if not self._state.adding:
            previous = (
                type(self).all_objects.filter(pk=self.pk)
                .values_list("custom_domain", "is_deleted")
                .first()
            )
            if previous is not None:
                previous_domain, previous_deleted = previous

        super().save(*args, **kwargs)

        old_host = (previous_domain or "").strip()
        new_host = (self.custom_domain or "").strip()
        deleted_flipped = previous_deleted != bool(self.is_deleted)

        from core.domain_index import invalidate_domain_cache

        # Soft-delete must drop domain cache even when custom_domain is unchanged;
        # otherwise resolve-domain keeps serving the tenant until TTL.
        if old_host.lower() == new_host.lower():
            if deleted_flipped and new_host:
                invalidate_domain_cache(new_host)
            return

        if old_host:
            invalidate_domain_cache(old_host)
        if new_host:
            invalidate_domain_cache(new_host)


