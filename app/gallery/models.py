"""Gallery domain models: events, scenes, photos, and client-access artifacts."""

import uuid
from urllib.parse import urlparse
from django.conf import settings
import logging

from django.db import models
from django.core.validators import MinValueValidator
from django.contrib.auth.hashers import make_password, check_password

# Assuming Workspace is defined in core.models
from core.models import Workspace


logger = logging.getLogger(__name__)


def _is_safe_client_url(value: str | None) -> bool:
    if not value:
        return False
    parsed = urlparse(str(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class VisibilityChoices(models.TextChoices):
    PUBLIC = 'PUBLIC', 'Public'
    CLIENT_ONLY = 'CLIENT_ONLY', 'Client Only'


class GalleryAccessRole(models.TextChoices):
    CLIENT = 'CLIENT', 'Client'
    GUEST = 'GUEST', 'Guest'


class GalleryArchiveType(models.TextChoices):
    FULL = 'FULL', 'Full Gallery'
    FAVORITES = 'FAVORITES', 'Favorites Only'


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
    cover_photo = models.URLField(blank=True, null=True)
    typography_theme = models.CharField(max_length=64, default='editorial-serif')
    color_theme = models.CharField(max_length=64, default='linen-ink')

    # ENGINEER FIX: Removed global unique=True. Replaced with UniqueConstraint below.
    slug = models.SlugField(max_length=255, db_index=True)

    # Lifecycle & Security Flags
    is_published = models.BooleanField(default=False)
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True)
    allow_downloads = models.BooleanField(
        default=True,
        help_text="When False, clients and guests may view but not download or export.",
    )

    # The PIN is never accessible in plain text.
    _hashed_pin = models.CharField(max_length=128, blank=True, null=True, db_column='hashed_pin')

    created_at = models.DateTimeField(auto_now_add=True)

    # NOTIFICATION SYSTEM: Contact info for the photographer's client.
    # Set manually by the photographer before publishing the gallery.
    client_email = models.EmailField(
        blank=True, null=True,
        help_text="Client's email address. Gallery-ready notification is sent here on publish."
    )
    client_name = models.CharField(
        max_length=255, blank=True, null=True,
        help_text="Client's display name used in the notification email."
    )

    class Meta:
        constraints = [
            # SECURITY: Prevents Slug Extortion DoS. Slugs are only unique per-workspace.
            models.UniqueConstraint(fields=['workspace', 'slug'], name='unique_event_slug_per_workspace')
        ]

    def set_pin(self, raw_pin):
        """Hashes the PIN using Django's secure cryptographic hasher."""
        if raw_pin:
            self._hashed_pin = make_password(str(raw_pin))
        else:
            self._hashed_pin = ''
        self.save(update_fields=['_hashed_pin'])

    def check_pin(self, raw_pin):
        """Verifies the PIN in constant time to prevent timing attacks."""
        if not self._hashed_pin:
            return False
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
    visibility = models.CharField(
        max_length=20,
        choices=VisibilityChoices.choices,
        default=VisibilityChoices.PUBLIC,
        db_index=True,
    )

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
        ('PENDING',     'Pending Upload'),
        ('PROCESSING',  'Processing'),
        ('READY',       'Ready'),
        ('FAILED',      'Failed'),
        ('QUARANTINED', 'Quarantined — Size Mismatch'),  # File larger than declared; held for review
        ('EXPIRED',     'Expired'),                      # Gallery TTL exceeded
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scene = models.ForeignKey(Scene, on_delete=models.CASCADE, related_name='photos')
    visibility = models.CharField(
        max_length=20,
        choices=VisibilityChoices.choices,
        default=VisibilityChoices.PUBLIC,
        db_index=True,
    )

    # --- PILLAR 1: THE EDA UPGRADE (Asynchronous State Machine) ---
    # These fields allow the Ingestion App and Celery Workers to track files without downloading them.
    r2_object_key = models.CharField(max_length=1024, blank=True, null=True)
    media_type = models.CharField(max_length=10, choices=[('IMAGE', 'Image'), ('VIDEO', 'Video')], default='IMAGE')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    optimized_url = models.URLField(max_length=1024, blank=True, null=True)
    web_r2_object_key = models.CharField(max_length=1024, blank=True, null=True)

    # --- PILLAR 2: YOUR LEGACY FIELDS (Backward Compatibility) ---
    # Made optional (blank/null=True) so the new EDA bulk ingestion doesn't crash on insert.
    image_file = models.FileField(upload_to='events/%Y/%m/', max_length=255, blank=True, null=True)
    original_filename = models.CharField(max_length=255)
    file_size_bytes = models.BigIntegerField(validators=[MinValueValidator(0)])

    is_processed = models.BooleanField(default=False, db_index=True)
    blurhash = models.CharField(max_length=100, blank=True, null=True, help_text="Base64 LQIP string")
    exif_data = models.JSONField(default=dict, blank=True)

    # --- PILLAR 3: DELIVERY LAYER (Zero-Layout-Shift Masonry Grid) ---
    # Captured during Celery processing via Pillow or R2 metadata.
    # Enables the React frontend to pre-allocate card space before the image loads.
    width = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Image pixel width. Set by Celery worker post-upload."
    )
    height = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Image pixel height. Set by Celery worker post-upload."
    )

    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']
        indexes = [
            models.Index(fields=['scene', 'is_processed']),  # Legacy frontend query
            models.Index(fields=['scene', 'status']),        # New EDA frontend polling query
            models.Index(
                fields=['scene', 'uploaded_at', 'id'],
                name='gal_photo_scene_upload_id_idx',
            ),
        ]

    def _watermark_is_required(self) -> bool:
        """True when the owning workspace brands its deliverables."""
        return bool(self.scene.event.workspace.watermark_logo)

    @property
    def delivery_url(self):
        """
        PHASE 3: Cloudinary Fetch Proxy URL.
        """
        cloud_name = getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
        r2_domain = getattr(settings, 'CLOUDFLARE_R2_DOMAIN', '').rstrip('/')

        if not self.web_r2_object_key and self._watermark_is_required():
            return None

        delivery_key = self.web_r2_object_key or self.r2_object_key
        if delivery_key and cloud_name and r2_domain:
            r2_public_url = f"https://{r2_domain}/{delivery_key}"
            return (
                f"https://res.cloudinary.com/{cloud_name}"
                f"/image/fetch/q_auto,f_webp/{r2_public_url}"
            )

        # Backward compat: old photos with Cloudinary SDK optimized_url still work
        if self.optimized_url and _is_safe_client_url(self.optimized_url):
            return self.optimized_url

        return None

    @property
    def download_url(self) -> str | None:
        """
        PHASE 3: Presigned R2 GET URL for client download.

        SECURITY — Presigned URL Exfiltration Defense:
          Hard ceiling of 60 seconds enforced in storage.py.
          This property passes no ExpiresIn — the utility enforces the cap.
          If a URL is intercepted or leaked, it becomes useless in ≤15 min.

        Both images and videos use this URL. Large video downloads (5GB)
        go directly to Cloudflare R2 edge — Django is not in the data path.
        """
        from gallery.storage import generate_r2_presigned_get_url

        if self.r2_object_key:
            # Primary: EDA-uploaded files in R2
            return generate_r2_presigned_get_url(
                bucket=getattr(settings, 'CLOUDFLARE_R2_BUCKET_NAME', ''),
                key=self.r2_object_key,
            )

        # Backward compat: legacy local file uploads
        if self.image_file:
            try:
                return self.image_file.url
            except Exception:
                logger.debug("Legacy local image URL was unavailable.", exc_info=True)

        return None

    @property
    def aspect_ratio(self) -> float | None:
        """
        PHASE 3: Aspect ratio for zero-layout-shift masonry grids.

        The React frontend uses this to pre-allocate the card height before
        the image bytes arrive, preventing the dreaded 'content jump' that
        degrades CLS (Core Web Vitals: Cumulative Layout Shift).

        Returns width/height rounded to 4 decimal places, or None if dimensions
        are not yet available (photo still PENDING processing).
        """
        if self.width and self.height and self.height > 0:
            return round(self.width / self.height, 4)
        return None

    # --- BACKWARD COMPATIBILITY ALIASES ---
    # Kept for any code still referencing the old property names.
    # Will be removed in the next major version.
    @property
    def r2_download_url(self):
        """Deprecated alias. Use download_url instead."""
        return self.download_url

    @property
    def cloudinary_thumbnail_url(self):
        """Deprecated alias. Use delivery_url instead."""
        return self.delivery_url


# --- THE ALIAS BRIDGE ---
# The new ingestion app explicitly imports "MediaAsset".
# By aliasing it here, the ingestion tests pass instantly, the database remains a single table,
# and we don't have to rewrite thousands of lines of your legacy frontend code.
MediaAsset = Photo
Gallery = Event


class ClientAllowlist(models.Model):
    """
    Approved main-client email addresses for a gallery.

    These entries define who is allowed to receive single-use magic links.
    """
    gallery = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='client_allowlist')
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['gallery', 'email'],
                name='unique_allowlisted_client_per_gallery',
            ),
        ]
        indexes = [
            models.Index(
                fields=['gallery', 'email'],
                name='gal_allow_gallery_email_idx',
            ),
        ]

    def __str__(self):
        return f"{self.gallery.title} -> {self.email}"


class GalleryMagicLink(models.Model):
    """
    Stores only the SHA-256 hash of an opaque client-access token.

    Raw tokens are never persisted in the database, which sharply reduces the
    blast radius of a database leak.
    """
    gallery = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='magic_links')
    email = models.EmailField()
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['gallery', 'email'],
                name='gal_magic_gallery_email_idx',
            ),
        ]

    def __str__(self):
        return f"Magic link for {self.email} ({self.gallery.title})"


class GalleryAccessSession(models.Model):
    """
    Audit trail for passwordless gallery access.

    Clients and guests are not full Django users; this table records who
    authenticated into which gallery and with which scope.
    """
    gallery = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='access_sessions')
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=GalleryAccessRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=['gallery', 'email'],
                name='gal_access_gallery_email_idx',
            ),
            models.Index(
                fields=['gallery', 'role'],
                name='gal_access_gallery_role_idx',
            ),
        ]

    def __str__(self):
        return f"{self.gallery.title} [{self.role}] {self.email}"


class FavoriteSelection(models.Model):
    """
    Proofing selections tied to a concrete authenticated gallery session.

    The unique constraint prevents selection spam and race-condition dupes
    where the same browser session submits the same photo twice.
    """
    session = models.ForeignKey(
        GalleryAccessSession,
        on_delete=models.CASCADE,
        related_name='favorite_selections',
    )
    photo = models.ForeignKey(
        Photo,
        on_delete=models.CASCADE,
        related_name='favorite_selections',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['session', 'photo'],
                name='unique_favorite_per_session_photo',
            ),
        ]
        indexes = [
            models.Index(
                fields=['session', 'created_at'],
                name='gal_fav_session_created_idx',
            ),
            models.Index(
                fields=['photo', 'created_at'],
                name='gallery_favo_photo_8930d4_idx',
            ),
        ]

    def __str__(self):
        return f"{self.session.email} -> {self.photo.original_filename}"


class GalleryArchiveJob(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PROCESSING = 'PROCESSING', 'Processing'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    gallery = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='archive_jobs')
    access_session = models.ForeignKey(
        GalleryAccessSession,
        on_delete=models.CASCADE,
        related_name='archive_jobs',
        blank=True,
        null=True,
    )
    archive_type = models.CharField(
        max_length=20,
        choices=GalleryArchiveType.choices,
        default=GalleryArchiveType.FULL,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    r2_zip_key = models.CharField(max_length=1024, blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(archive_type=GalleryArchiveType.FULL)
                    | models.Q(access_session__isnull=False)
                ),
                name='favorites_archives_require_access_session',
            ),
        ]
        indexes = [
            models.Index(
                fields=['gallery', 'status'],
                name='gal_archive_gallery_status_idx',
            ),
            models.Index(
                fields=['gallery', 'archive_type', 'status'],
                name='gallery_gal_gallery_e48be8_idx',
            ),
        ]

    def __str__(self):
        return f"{self.gallery.title} {self.archive_type.lower()} archive [{self.status}]"
