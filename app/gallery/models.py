import uuid
import logging
import cloudinary.utils
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator
from django.contrib.auth.hashers import make_password, check_password

# Assuming Workspace is defined in core.models
from core.models import Workspace

logger = logging.getLogger(__name__)

class Event(models.Model):
    """
    THE GIG: Top-level access control and tenant isolation.
    """
    EVENT_TYPES = (
        ('WEDDING', 'Wedding'),
        ('CORPORATE', 'Corporate Event'),
        ('GOVERNMENT', 'Government / Official'),
        ('NGO', 'NGO / Non-Profit'),
        ('PRIVATE', 'Private Function'),
        ('SPORTS', 'Sports / Action'),
        ('STUDIO', 'Studio / Portrait'),
        ('COMMERCIAL', 'Commercial / Brand Campaign'),
        ('OTHER', 'Other')
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='events')

    title = models.CharField(max_length=255)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPES, default='OTHER')
    event_date = models.DateField(blank=True, null=True)

    cover_image_url = models.URLField(blank=True, null=True)

    # ENGINEER FIX: Removed global unique=True. Replaced with UniqueConstraint below.
    slug = models.SlugField(max_length=255, db_index=True)

    # Lifecycle & Security Flags
    is_published = models.BooleanField(default=False)
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True)

    # The PIN is never accessible in plain text.
    _hashed_pin = models.CharField(max_length=128, blank=True, null=True, db_column='hashed_pin')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # SECURITY: Prevents Slug Extortion DoS. Slugs are only unique per-workspace.
            models.UniqueConstraint(fields=['workspace', 'slug'], name='unique_event_slug_per_workspace')
        ]

    def set_pin(self, raw_pin):
        """Hashes the PIN using Django's secure cryptographic hasher."""
        if raw_pin:
            self._hashed_pin = make_password(str(raw_pin))
            self.save(update_fields=['_hashed_pin'])

    def check_pin(self, raw_pin):
        """Verifies the PIN in constant time to prevent timing attacks."""
        if not self._hashed_pin:
            return True
        return check_password(str(raw_pin), self._hashed_pin)

    def __str__(self):
        return f"[{self.event_type}] {self.title} ({self.workspace.business_name})"


class Scene(models.Model):
    """
    THE STAGE: Drives the frontend tab navigation (e.g., 'Keynote', 'Red Carpet').
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='scenes')

    title = models.CharField(max_length=100)
    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'title']
        constraints = [
            # ENGINEER FIX: Modernized from deprecated unique_together
            models.UniqueConstraint(fields=['event', 'title'], name='unique_scene_per_event')
        ]

    def __str__(self):
        return f"{self.event.title} - {self.title}"


class Photo(models.Model):
    """
    THE ASSET: Unified model supporting Legacy Uploads AND Event-Driven Direct-to-R2 Uploads.
    """
    STATUS_CHOICES = [
        ('PENDING', 'Pending Upload'),
        ('PROCESSING', 'Processing'),
        ('READY', 'Ready'),
        ('FAILED', 'Failed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name='photos')

    # --- PILLAR 1: THE EDA UPGRADE (Asynchronous State Machine) ---
    # These fields allow the Ingestion App and Celery Workers to track files without downloading them.
    r2_object_key = models.CharField(max_length=1024, blank=True, null=True)
    media_type = models.CharField(max_length=10, choices=[('IMAGE', 'Image'), ('VIDEO', 'Video')], default='IMAGE')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    optimized_url = models.URLField(max_length=1024, blank=True, null=True)

    # --- PILLAR 2: YOUR LEGACY FIELDS (Backward Compatibility) ---
    # Made optional (blank/null=True) so the new EDA bulk ingestion doesn't crash on insert.
    image_file = models.FileField(upload_to='events/%Y/%m/', max_length=255, blank=True, null=True)
    original_filename = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField(validators=[MinValueValidator(0)])

    is_processed = models.BooleanField(default=False, db_index=True)
    blurhash = models.CharField(max_length=100, blank=True, null=True, help_text="Base64 LQIP string")
    exif_data = models.JSONField(default=dict, blank=True)

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']
        indexes = [
            models.Index(fields=['scene', 'is_processed']), # Legacy frontend query
            models.Index(fields=['scene', 'status']),       # New EDA frontend polling query
        ]

    @property
    def r2_download_url(self):
        """Direct vault access for ZIP generation. Supports both legacy and EDA files."""
        if self.image_file:
            return self.image_file.url
        if self.optimized_url:
            return self.optimized_url
        return None

    @property
    def cloudinary_thumbnail_url(self):
        """
        Generates a cryptographically signed Cloudinary fetch URL.
        Prevents parameter tampering (watermark removal, dimension hijacking).
        """
        if not self.image_file and not self.r2_object_key:
            return None

        # ENGINEER FIX: Gracefully handles trailing slashes in settings
        domain = getattr(settings, 'CLOUDFLARE_R2_DOMAIN', '').rstrip('/')

        # Determine the source based on how the file was uploaded (Legacy vs EDA)
        source_path = self.image_file.url if self.image_file else f"https://{domain}/{self.r2_object_key}"

        try:
            url, options = cloudinary.utils.cloudinary_url(
                source_path,
                type="fetch",
                sign_url=True, # Critical: Enforces the s--HMAC-- signature
                width=800,
                fetch_format="auto",
                quality="auto:eco",
                crop="limit"
            )
            return url
        except Exception as e:
            logger.error(f"Cloudinary signature failed for Asset {self.id}: {str(e)}")
            return source_path


# --- THE ALIAS BRIDGE ---
# The new ingestion app explicitly imports "MediaAsset".
# By aliasing it here, the ingestion tests pass instantly, the database remains a single table,
# and we don't have to rewrite thousands of lines of your legacy frontend code.
MediaAsset = Photo









# import uuid
# import cloudinary.utils
# from django.db import models
# from django.conf import settings
# from django.core.validators import MinValueValidator
# from django.contrib.auth.hashers import make_password, check_password

# # Assuming Workspace is defined in core.models
# from core.models import Workspace

# class Event(models.Model):
#     """
#     THE GIG: Top-level access control and tenant isolation.
#     Replaces the flat 'Gallery' structure with professional hierarchy.
#     """
#     EVENT_TYPES = (
#         ('WEDDING', 'Wedding'),
#         ('CORPORATE', 'Corporate Event'),
#         ('GOVERNMENT', 'Government / Official'),
#         ('NGO', 'NGO / Non-Profit'),
#         ('PRIVATE', 'Private Function'),
#         ('SPORTS', 'Sports / Action'),
#         ('STUDIO', 'Studio / Portrait'),
#         ('COMMERCIAL', 'Commercial / Brand Campaign'),
#         ('OTHER', 'Other')
#     )

#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name='events')

#     title = models.CharField(max_length=255)
#     event_type = models.CharField(max_length=20, choices=EVENT_TYPES, default='OTHER')
#     event_date = models.DateField(blank=True, null=True)

#     cover_image_url = models.URLField(blank=True, null=True)
#     slug = models.SlugField(max_length=255, unique=True, db_index=True)

#     # Lifecycle & Security Flags
#     is_published = models.BooleanField(default=False)
#     expires_at = models.DateTimeField(blank=True, null=True, db_index=True)

#     # The PIN is never accessible in plain text.
#     _hashed_pin = models.CharField(max_length=128, blank=True, null=True, db_column='hashed_pin')

#     created_at = models.DateTimeField(auto_now_add=True)

#     def set_pin(self, raw_pin):
#         """Hashes the PIN using Django's secure cryptographic hasher (Argon2/PBKDF2)."""
#         if raw_pin:
#             self._hashed_pin = make_password(str(raw_pin))
#             self.save(update_fields=['_hashed_pin'])

#     def check_pin(self, raw_pin):
#         """Verifies the PIN in constant time to prevent timing attacks."""
#         if not self._hashed_pin:
#             return True
#         return check_password(str(raw_pin), self._hashed_pin)

#     def __str__(self):
#         return f"[{self.event_type}] {self.title} ({self.workspace.business_name})"


# class Scene(models.Model):
#     """
#     THE STAGE: Drives the frontend tab navigation (e.g., 'Keynote', 'Red Carpet').
#     """
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='scenes')

#     title = models.CharField(max_length=100)
#     display_order = models.IntegerField(default=0)

#     class Meta:
#         ordering = ['display_order', 'title']
#         unique_together = ['event', 'title'] # Prevents duplicate tabs in the UI

#     def __str__(self):
#         return f"{self.event.title} - {self.title}"


# class Photo(models.Model):
#     """
#     THE ASSET: Represents the physical image in R2.
#     Frontend is blind to this until is_processed = True.
#     """
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name='photos')

#     # The exact path generated by the Celery worker after moving from /quarantine/
#     image_file = models.FileField(upload_to='events/%Y/%m/', max_length=255)

#     # Sanitized original name for the photographer's dashboard
#     original_filename = models.CharField(max_length=255)
#     file_size_bytes = models.BigIntegerField(validators=[MinValueValidator(0)])

#     # Async State Control
#     is_processed = models.BooleanField(default=False, db_index=True)
#     blurhash = models.CharField(max_length=100, blank=True, null=True, help_text="Base64 LQIP string")
#     exif_data = models.JSONField(default=dict, blank=True)

#     uploaded_at = models.DateTimeField(auto_now_add=True)

#     class Meta:
#         ordering = ['uploaded_at']
#         indexes = [
#             # Composite index for lightning-fast frontend queries
#             models.Index(fields=['scene', 'is_processed']),
#         ]

#     @property
#     def r2_download_url(self):
#         """Direct vault access for ZIP generation. Bypasses CDN bandwidth."""
#         if self.image_file:
#             return self.image_file.url
#         return None

#     @property
#     def cloudinary_thumbnail_url(self):
#         """
#         Generates a cryptographically signed Cloudinary fetch URL.
#         Prevents parameter tampering (watermark removal, dimension hijacking).
#         """
#         if not self.image_file:
#             return None

#         try:
#             url, options = cloudinary.utils.cloudinary_url(
#                 self.image_file.url,
#                 type="fetch",
#                 sign_url=True, # Critical: Enforces the s--HMAC-- signature
#                 width=800,
#                 fetch_format="auto",
#                 quality="auto:eco",
#                 crop="limit"
#             )
#             return url
#         except Exception:
#             return self.image_file.url
