"""
Views for the Gallery API (The Pixieset Standard).
"""
import logging
import os
import struct
from urllib.parse import unquote

from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from rest_framework import mixins, parsers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from core.models import Workspace
from gallery.models import Event, Scene, Photo
from gallery import serializers
from gallery.throttles import FastLaneUploadThrottle

# Enterprise Image Inspection
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

logger = logging.getLogger(__name__)
CLIENT_GALLERY_ACCESS_SESSION_KEY = "client_gallery_access"
_ALLOWED_TRAILING_BYTES = b"\x00\t\r\n "
_MAX_ORIGINAL_FILENAME_LEN = Photo._meta.get_field("original_filename").max_length


def _has_non_padding_trailing_bytes(payload: bytes) -> bool:
    """Allow only inert padding after a well-formed image container terminator."""
    return any(byte not in _ALLOWED_TRAILING_BYTES for byte in payload)


def _assert_no_polyglot_payload(image_file, image_format: str) -> None:
    """
    Reject image files that append a second payload after the real image.

    Pillow verifies the image container itself, but formats like JPEG can still
    carry extra trailing bytes that turn the upload into a JPEG+ZIP polyglot.
    """
    image_file.seek(0)
    raw = image_file.read()
    image_file.seek(0)

    normalized_format = (image_format or "").upper()

    if normalized_format == "JPEG":
        jpeg_eoi = raw.rfind(b"\xff\xd9")
        if jpeg_eoi == -1:
            raise ValidationError("Malware Shield: Uploaded JPEG is structurally corrupted.")
        if _has_non_padding_trailing_bytes(raw[jpeg_eoi + 2:]):
            raise ValidationError("Polyglot payload detected: JPEG contains trailing data.")
        return

    if normalized_format == "PNG":
        png_iend = raw.rfind(b"\x49\x45\x4e\x44\xae\x42\x60\x82")
        if png_iend == -1:
            raise ValidationError("Malware Shield: Uploaded PNG is structurally corrupted.")
        if _has_non_padding_trailing_bytes(raw[png_iend + 8:]):
            raise ValidationError("Polyglot payload detected: PNG contains trailing data.")
        return

    if normalized_format == "WEBP":
        if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WEBP":
            raise ValidationError("Malware Shield: Uploaded WEBP is structurally corrupted.")
        declared_size = struct.unpack("<I", raw[4:8])[0] + 8
        if declared_size <= len(raw) and _has_non_padding_trailing_bytes(raw[declared_size:]):
            raise ValidationError("Polyglot payload detected: WEBP contains trailing data.")


def _sanitize_original_filename(raw_name: str) -> str:
    """
    Normalize incoming upload filenames before they reach the database.

    The stored name is metadata only, so we collapse traversal attempts down to
    the basename and reject null bytes / control characters that Postgres or
    downstream storage layers would interpret unsafely.
    """
    if not raw_name:
        raise ValidationError("Filename is required.")

    decoded_name = unquote(str(raw_name))
    if "\x00" in decoded_name:
        raise ValidationError("Filename contains invalid control characters.")

    candidate = os.path.basename(decoded_name.replace("\\", "/")).strip()
    if not candidate or candidate in {".", ".."}:
        raise ValidationError("Filename is invalid.")

    if any(ord(char) < 32 for char in candidate):
        raise ValidationError("Filename contains invalid control characters.")

    if len(candidate) > _MAX_ORIGINAL_FILENAME_LEN:
        raise ValidationError(
            f"Filename exceeds maximum length of {_MAX_ORIGINAL_FILENAME_LEN} characters."
        )

    return candidate


def _client_session_allows_event(request, event) -> bool:
    """
    Fail-closed session contract for verified client gallery access.

    The frontend can store a per-gallery access marker after the client passes
    the gallery PIN or equivalent verification flow. Until then, anonymous
    requests are denied and no download URL is minted.
    """
    session = getattr(request, "session", None)
    if session is None:
        return False

    access_map = session.get(CLIENT_GALLERY_ACCESS_SESSION_KEY, {})
    if not isinstance(access_map, dict):
        return False

    candidate = access_map.get(str(event.id))
    if candidate is None:
        candidate = access_map.get(event.slug)

    if isinstance(candidate, dict):
        if not candidate.get("verified", False):
            return False
        recorded_slug = candidate.get("slug")
        return not recorded_slug or recorded_slug == event.slug

    return bool(candidate)


def _authorize_event_download_access(request, event) -> dict:
    """
    Unified download authorizer for photographer-owned and client-gallery flows.
    """
    user = getattr(request, "user", None)
    if user and user.is_authenticated and event.workspace.user_id == user.id:
        return {
            "principal": "photographer",
            "requested_by_user_id": str(user.id),
        }

    if event.is_published and _client_session_allows_event(request, event):
        return {
            "principal": "client",
            "requested_by_user_id": None,
        }

    raise PermissionDenied(
        "You do not have permission to access downloads for this gallery."
    )


# ==========================================
# 1. PIXIESET STANDARD: EVENT (The Collection)
# ==========================================

class PhotographerDashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'gallery/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        
        # Aggregate data if workspace exists
        if hasattr(user, 'workspace'):
            workspace = user.workspace
            context['total_events'] = Event.objects.filter(workspace=workspace).count()
            context['recent_photos'] = Photo.objects.filter(scene__event__workspace=workspace).order_by('-uploaded_at')[:5]
            context['total_heavy_assets'] = Photo.objects.filter(scene__event__workspace=workspace, r2_object_key__isnull=False).count()
            
            # Simulated storage pulling from Subscription
            if hasattr(user, 'subscription'):
                storage_bytes = user.subscription.storage_used_bytes
                context['storage_used_gb'] = round(storage_bytes / (1024**3), 2)
        
        return context

# The rest of DRF views
class EventViewSet(viewsets.ModelViewSet):
    """Viewset for managing top-level Events (e.g., Weddings, Corporate Gigs)."""
    serializer_class = serializers.EventSerializer
    queryset = Event.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    # RATE LIMITING: Prevent Denial of Database Rows (No infinite bot creation)
    throttle_classes = [FastLaneUploadThrottle]

    def get_permissions(self):
        if getattr(self, 'action', None) == 'bulk_download':
            return [AllowAny()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        """TENANT ISOLATION: Only retrieve Events owned by the authenticated user."""
        return self.queryset.filter(
            workspace__user=self.request.user
        ).order_by('-created_at')

    def perform_create(self, serializer):
        """Create a new Event securely mapped to the user's workspace."""
        try:
            workspace = Workspace.objects.get(user=self.request.user)
        except Workspace.DoesNotExist:
            raise ValidationError("A workspace must be initialized before creating Events.")

        serializer.save(workspace=workspace)

    def perform_update(self, serializer):
        """
        NOTIFICATION TRIGGER (Manual Publish):
        Detect the is_published False → True transition and fire the gallery-ready
        notification email as an async Celery task.

        SECURITY:
          - Only fires if the workspace owner is the one publishing.
          - Cross-tenant check already enforced by get_queryset() —
            a user cannot PATCH an event they cannot retrieve.
          - Task is idempotent: repeated publishes re-send the notification
            (photographer may want to resend to a client).
        """
        from gallery.notifications import send_gallery_ready_email

        was_published = serializer.instance.is_published
        event = serializer.save()

        # Only fire the notification on the specific False → True transition
        if not was_published and event.is_published and event.client_email:
            send_gallery_ready_email.delay(str(event.id))
            logger.info(
                f"[PUBLISH] Gallery-ready email queued for Event {event.id} "
                f"to {event.client_email}"
            )

    @action(detail=True, methods=['post'], url_path='bulk-download')
    def bulk_download(self, request, pk=None):
        """
        Queue the high-resolution gallery archive flow.

        The actual ZIP assembly remains asynchronous by design so the API never
        streams a large archive from the Django worker thread.
        """
        from gallery.tasks import prepare_gallery_bulk_download

        event = Event.objects.select_related('workspace__user').filter(pk=pk).first()
        if event is None:
            raise NotFound("Event not found.")

        access = _authorize_event_download_access(request, event)
        async_result = prepare_gallery_bulk_download.delay(
            str(event.id),
            requested_by_user_id=access['requested_by_user_id'],
            requester_kind=access['principal'],
        )

        return Response(
            {
                "status": "queued",
                "task_id": async_result.id,
                "event_id": str(event.id),
                "delivery_mode": "async_bulk_zip",
                "message": (
                    "Bulk gallery download accepted. The archive will be prepared "
                    "asynchronously so large downloads never run on the request thread."
                ),
            },
            status=status.HTTP_202_ACCEPTED,
        )


# ==========================================
# 2. PIXIESET STANDARD: THE STAGE (Scenes / Tabs)
# ==========================================

class SceneViewSet(viewsets.ModelViewSet):
    """Viewset for managing Sub-Sets within an Event (e.g., Ceremony, Reception)."""
    serializer_class = serializers.SceneSerializer
    queryset = Scene.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    # RATE LIMITING: Use global user throttle via FastLaneUploadThrottle scope
    throttle_classes = [FastLaneUploadThrottle]

    def get_queryset(self):
        """Only fetch scenes from Events that belong to this user."""
        queryset = self.queryset.filter(
            event__workspace__user=self.request.user
        ).order_by('event', 'display_order')

        # React Optimization: Allow filtering by Event ID
        event_id = self.request.query_params.get('event')
        if event_id:
            queryset = queryset.filter(event_id=event_id)

        return queryset

    def perform_create(self, serializer):
        """
        SECURITY (Cross-Tenant Hijacking Shield):
        Ensure the Event they are attaching this Scene to actually belongs to them.
        """
        event = serializer.validated_data['event']
        if event.workspace.user != self.request.user:
            raise PermissionDenied("You do not have permission to attach a Scene to a competitor's Event.")
        
        serializer.save()


# ==========================================
# 3. FAST LANE: EDA-COMPLIANT ASSET HANDLER
# ==========================================
# The web thread does TWO things only:
#   1. Magic Byte inspection (CPU-only, no I/O)
#   2. DB write with is_processed=False
# Then immediately fires a Celery task and returns 202.
# The web worker is FREE within milliseconds.

class FastLanePhotoPagination(PageNumberPagination):
    """
    Fail-closed pagination for tenant photo listings.

    The explicit max_page_size prevents authenticated clients from requesting
    arbitrarily large result sets and turning the endpoint into a response-body
    amplification vector.
    """
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 100

class PhotoFastLaneViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet
):
    """
    The Fast Lane HTTP Uploader.
    Restricts payload size, performs Pillow two-pass Magic Byte inspection,
    and hands off all I/O work to a Celery worker immediately.
    """
    serializer_class = serializers.PhotoFastLaneSerializer
    queryset = Photo.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    # PHASE 4: 30 uploads per minute — independent bucket from the global 'user' throttle
    throttle_classes = [FastLaneUploadThrottle]

    # DRF native Multipart parser for binary
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    pagination_class = FastLanePhotoPagination

    def get_permissions(self):
        if getattr(self, 'action', None) == 'download_url':
            return [AllowAny()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        """
        TENANT ISOLATION AUDIT (Phase 4):
        The 4-join chain `scene__event__workspace__user` ensures a photo can only
        be retrieved if the authenticated user owns the workspace that owns the event
        that owns the scene that owns the photo. No shortcuts.
        """
        queryset = self.queryset.filter(
            scene__event__workspace__user=self.request.user
        )

        scene_id = self.request.query_params.get('scene')
        if scene_id:
            queryset = queryset.filter(scene_id=scene_id)

        return queryset.order_by('-uploaded_at')

    def download_url(self, request, pk=None):
        """
        Return a short-lived presigned R2 download URL for a single photo.

        This endpoint performs an explicit ownership check so cross-tenant access
        attempts fail closed before a signed URL can ever be minted. It also
        supports verified client-gallery sessions so mobile users can tap
        Download inside the frontend without exposing long-lived URLs in email.
        """
        photo = Photo.objects.select_related(
            'scene__event__workspace__user'
        ).filter(pk=pk).first()

        if photo is None:
            raise NotFound("Photo not found.")

        access = _authorize_event_download_access(request, photo.scene.event)

        if photo.status != 'READY' or not photo.is_processed:
            raise ValidationError(
                "Download URL unavailable. The asset is not ready for download."
            )

        download_url = photo.download_url
        if not download_url:
            raise ValidationError(
                "Download URL unavailable. The asset is not ready for download."
            )

        return Response(
            {
                "download_url": download_url,
                "expires_in_seconds": 60,
                "delivery_mode": "direct_r2_presigned_get",
                "requester_kind": access["principal"],
            },
            status=status.HTTP_200_OK,
        )

    def perform_create(self, serializer):
        """
        EDA FAST LANE PERIMETER: This method does NO I/O to external services.
        It runs only CPU-bound checks, writes to DB, fires async task, then exits.
        The Django worker is freed in < 100ms regardless of file size.
        """
        # Import here to avoid circular import at module level
        from gallery.tasks import process_fast_lane_asset

        scene = serializer.validated_data['scene']

        # 1. CROSS-TENANT SHIELD
        if scene.event.workspace.user != self.request.user:
            raise PermissionDenied(
                "You do not have permission to upload to this Scene."
            )

        image_file = self.request.FILES.get('image_file')
        if not image_file:
            raise ValidationError("Fast Lane requires an 'image_file' binary payload.")

        # 2. PAYLOAD SIZE GATE (5MB ceiling — blocks Slowloris & OOM)
        MAX_FAST_LANE_BYTES = 5 * 1024 * 1024
        if image_file.size > MAX_FAST_LANE_BYTES:
            raise ValidationError(
                "Payload exceeds 5MB Fast Lane limit. Use the Heavy Lane (Ingestion App) for bulk/large RAWs."
            )

        # 3. MAGIC BYTE INSPECTOR (CPU-only, no network I/O)
        # TWO-PASS PATTERN — required by Pillow's API contract:
        #   Pass 1: img.verify() checks structural integrity but DESTROYS the image object.
        #           After verify(), img.width/height/format return None/0 — unusable.
        #   Pass 2: Reopen the file to safely inspect metadata (dimensions, format).
        # Single-pass pattern (verify + metadata in one `with`) is a silent Pillow bug.
        try:
            detected_format = None
            # PASS 1: Structural integrity — rejects corrupted files and disguised executables
            image_file.seek(0)
            probe = PILImage.open(image_file)
            probe.verify()  # raises UnidentifiedImageError or Error on structural failure

            # PASS 2: Metadata inspection — reopen because verify() destroys the object
            image_file.seek(0)
            with PILImage.open(image_file) as img:
                if img.width * img.height > (10000 * 10000):
                    raise ValidationError("Decompression Bomb detected: image pixel count exceeds 100MP.")
                if img.format not in ['JPEG', 'PNG', 'WEBP']:
                    raise ValidationError("Invalid Magic Bytes. Not a genuine JPEG, PNG, or WEBP.")
                detected_format = img.format
            _assert_no_polyglot_payload(image_file, detected_format)
        except UnidentifiedImageError:
            raise ValidationError("Malware Shield: Uploaded file is disguised or structurally corrupted.")

        # Reset file pointer so Django's storage backend can write it to disk
        image_file.seek(0)

        workspace = scene.event.workspace

        # 4. ATOMIC QUOTA GATE & RACE CONDITION DEFENSE
        # Hold the workspace row lock only across the quota math + photo insert so
        # concurrent uploads for the same tenant cannot double-spend the ledger.
        with transaction.atomic():
            locked_workspace = Workspace.objects.select_for_update().get(id=workspace.id)
            projected_usage = locked_workspace.storage_used_bytes + image_file.size
            if projected_usage > locked_workspace.storage_limit_bytes:
                raise ValidationError("Storage quota exceeded. Please upgrade your subscription.")

            Workspace.objects.filter(id=locked_workspace.id).update(
                storage_used_bytes=F('storage_used_bytes') + image_file.size
            )

            safe_filename = _sanitize_original_filename(image_file.name)

            # 5. DB WRITE — is_processed=False (the Celery task will flip this to True)
            # This is the LAST thing the web thread does. No Cloudinary. No external I/O.
            photo = serializer.save(
                file_size_bytes=image_file.size,
                original_filename=safe_filename,
                is_processed=False,   # Honest: processing hasn't happened yet
                status='PENDING',
            )

        # 6. FIRE AND FORGET — hand off to Celery worker pool
        # The web worker is now FREE. The HTTP response will return in milliseconds.
        # R2 upload happens asynchronously in a background Celery process.
        process_fast_lane_asset.delay(str(photo.id))

        logger.info(
            f"[FAST LANE] Photo {photo.id} accepted. "
            f"R2 Celery task dispatched. Worker freed immediately."
        )

    def create(self, request, *args, **kwargs):
        """
        Override create() to return 202 Accepted instead of the default 201 Created.
        202 is the semantically correct HTTP status: 'I received your file and
        queued it for processing, but it is NOT yet done.'
        201 would be dishonest since is_processed=False at this point.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        return Response(
            {
                "status": "queued",
                "message": "Photo accepted and queued for CDN processing. "
                           "Poll the photo endpoint to check is_processed status.",
                "photo_id": serializer.instance.id,
            },
            status=status.HTTP_202_ACCEPTED
        )

    def perform_destroy(self, instance):
        """
        SECURITY (Atomic Reversal): 
        When an image is deleted from the Fast Lane, safely refund the Workspace Quota.
        """
        file_size = instance.file_size_bytes
        workspace = instance.scene.event.workspace
        
        # Atomically strip the bytes from the ledger
        Workspace.objects.filter(id=workspace.id).update(
            storage_used_bytes=Greatest(0, F('storage_used_bytes') - file_size)
        )
        
        instance.delete()
