"""
Views for the Gallery API (The Pixieset Standard).
"""
import io
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, parsers, status, viewsets
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.quota import QuotaExceededError, release_workspace_bytes, reserve_workspace_bytes
from core.models import Workspace
from gallery.client_auth import (
    GalleryCookieJWTAuthentication,
    resolve_gallery_access_session,
)
from gallery.client_serializers import safe_client_text
from gallery.permissions import IsPhotographerUser
from gallery.filename_utils import sanitize_gallery_filename
from gallery.models import (
    Event,
    FavoriteSelection,
    GalleryAccessRole,
    Photo,
    Scene,
    VisibilityChoices,
)
from gallery.storage import DOWNLOAD_URL_TTL_SECONDS
from gallery import serializers
from gallery.throttles import FastLaneUploadThrottle

# Enterprise Image Inspection
from PIL import Image as PILImage
from PIL import UnidentifiedImageError

PILImage.MAX_IMAGE_PIXELS = int(
    getattr(settings, "PHOTO_MAX_IMAGE_PIXELS", 89_478_485)
)

logger = logging.getLogger(__name__)
FAST_LANE_ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def _image_payload_end_offset(image_format: str, image_bytes: bytes) -> int | None:
    if image_format == "JPEG":
        end_marker = image_bytes.rfind(b"\xff\xd9")
        if end_marker == -1:
            return None
        return end_marker + 2

    if image_format == "PNG":
        end_marker = image_bytes.rfind(b"IEND\xaeB`\x82")
        if end_marker == -1:
            return None
        return end_marker + 8

    if image_format == "WEBP":
        if len(image_bytes) < 12 or image_bytes[:4] != b"RIFF" or image_bytes[8:12] != b"WEBP":
            return None
        return int.from_bytes(image_bytes[4:8], "little") + 8

    return None


def _assert_no_trailing_payload(image_format: str, image_bytes: bytes) -> None:
    payload_end = _image_payload_end_offset(image_format, image_bytes)
    if payload_end is None or payload_end > len(image_bytes):
        raise ValidationError("Malware Shield: Uploaded file is structurally corrupted.")

    trailing_bytes = image_bytes[payload_end:]
    if trailing_bytes.strip(b"\x00 \t\r\n"):
        raise ValidationError(
            "Malware Shield: Uploaded file contains trailing payload data."
        )


# ==========================================
# 1. PIXIESET STANDARD: EVENT (The Collection)
# ==========================================

# The rest of DRF views
class EventViewSet(viewsets.ModelViewSet):
    """Viewset for managing top-level Events (e.g., Weddings, Corporate Gigs)."""
    serializer_class = serializers.EventSerializer
    queryset = Event.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsPhotographerUser]

    # RATE LIMITING: Prevent Denial of Database Rows (No infinite bot creation)
    throttle_classes = [FastLaneUploadThrottle]

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
        from gallery.ttl import stamp_event_expiry_on_publish

        was_published = serializer.instance.is_published
        event = serializer.save()

        if not was_published and event.is_published:
            stamp_event_expiry_on_publish(event)

        # Only fire the notification on the specific False → True transition
        if not was_published and event.is_published and event.client_email:
            send_gallery_ready_email.delay(str(event.id))
            logger.info(
                f"[PUBLISH] Gallery-ready email queued for Event {event.id} "
                f"to {event.client_email}"
            )


class PhotographerFavoritesSummaryView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request, gallery_id, *args, **kwargs):
        gallery = (
            Event.objects
            .filter(
                id=gallery_id,
                workspace__user=request.user,
            )
            .first()
        )
        if gallery is None:
            raise NotFound("Gallery not found.")

        selections = (
            FavoriteSelection.objects
            .select_related("session", "photo__scene")
            .filter(session__gallery=gallery)
            .order_by("photo__scene__display_order", "photo__uploaded_at", "created_at")
        )

        grouped = {}
        for selection in selections:
            photo_key = str(selection.photo_id)
            if photo_key not in grouped:
                grouped[photo_key] = {
                    "photo_id": photo_key,
                    "original_filename": selection.photo.original_filename,
                    "scene_id": str(selection.photo.scene_id),
                    "scene_title": selection.photo.scene.title,
                    "favorite_count": 0,
                    "selections": [],
                }

            grouped[photo_key]["favorite_count"] += 1
            grouped[photo_key]["selections"].append(
                {
                    "email": selection.session.email,
                    "role": selection.session.role,
                    "notes": safe_client_text(selection.notes),
                    "selected_at": selection.created_at,
                }
            )

        return Response(
            {
                "gallery_id": str(gallery.id),
                "favorites": list(grouped.values()),
            },
            status=status.HTTP_200_OK,
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

    authentication_classes = [JWTAuthentication, GalleryCookieJWTAuthentication]
    permission_classes = [IsPhotographerUser]

    # PHASE 4: 30 uploads per minute — independent bucket from the global 'user' throttle
    throttle_classes = [FastLaneUploadThrottle]

    # DRF native Multipart parser for binary
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    pagination_class = FastLanePhotoPagination

    def get_permissions(self):
        if getattr(self, "action", None) == "download_url":
            return [IsAuthenticated()]
        return [permission() for permission in self.permission_classes]

    def get_queryset(self):
        """
        TENANT ISOLATION AUDIT (Phase 4):
        The 4-join chain `scene__event__workspace__user` ensures a photo can only
        be retrieved if the authenticated user owns the workspace that owns the event
        that owns the scene that owns the photo. No shortcuts.
        """
        queryset = self.queryset.select_related(
            "scene__event__workspace"
        ).filter(
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
        attempts fail closed before a signed URL can ever be minted.
        """
        photo = Photo.objects.select_related(
            'scene__event__workspace__user'
        ).filter(pk=pk).first()

        if photo is None:
            raise NotFound("Photo not found.")

        requester_kind = "photographer"
        if getattr(request.user, "gallery_id", None) is not None:
            gallery = photo.scene.event
            if str(request.user.gallery_id) != str(gallery.id):
                raise PermissionDenied("Gallery scope mismatch.")

            if not gallery.is_published or (
                gallery.expires_at and gallery.expires_at <= timezone.now()
            ):
                raise PermissionDenied("Gallery access unavailable.")

            if not gallery.allow_downloads:
                raise PermissionDenied("Downloads are disabled for this gallery.")

            access_session = resolve_gallery_access_session(request, gallery)

            allowed_visibility = [VisibilityChoices.PUBLIC]
            if access_session.role == GalleryAccessRole.CLIENT:
                allowed_visibility.append(VisibilityChoices.CLIENT_ONLY)
                requester_kind = "client"
            else:
                requester_kind = "guest"

            if photo.visibility not in allowed_visibility:
                raise PermissionDenied(
                    "You do not have permission to generate a download URL for this photo."
                )
        elif photo.scene.event.workspace.user_id != request.user.id:
            raise PermissionDenied(
                "You do not have permission to generate a download URL for this photo."
            )

        if photo.status != "READY" or not photo.is_processed:
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
                "delivery_mode": "direct_r2_presigned_get",
                "expires_in_seconds": DOWNLOAD_URL_TTL_SECONDS,
                "requester_kind": requester_kind,
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

        safe_filename = sanitize_gallery_filename(image_file.name)
        if not safe_filename:
            raise ValidationError(
                "Unsafe filename. Use only letters, numbers, spaces, dots, dashes, and underscores."
            )

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
            # PASS 1: Structural integrity — rejects corrupted files and disguised executables
            image_file.seek(0)
            image_bytes = image_file.read()
            probe = PILImage.open(io.BytesIO(image_bytes))
            probe.verify()  # raises UnidentifiedImageError or Error on structural failure

            # PASS 2: Metadata inspection — reopen because verify() destroys the object
            with PILImage.open(io.BytesIO(image_bytes)) as img:
                if img.width * img.height > PILImage.MAX_IMAGE_PIXELS:
                    raise ValidationError("Decompression Bomb detected: image pixel count exceeds limit.")
                if img.format not in FAST_LANE_ALLOWED_IMAGE_FORMATS:
                    raise ValidationError("Invalid Magic Bytes. Not a genuine JPEG, PNG, or WEBP.")
                _assert_no_trailing_payload(img.format, image_bytes)
        except (OSError, UnidentifiedImageError):
            raise ValidationError("Malware Shield: Uploaded file is disguised or structurally corrupted.")

        # Reset file pointer so Django's storage backend can write it to disk
        image_file.seek(0)

        workspace = scene.event.workspace

        # 4. ATOMIC QUOTA GATE & RACE CONDITION DEFENSE
        # Hold the workspace row lock only across the quota math + photo insert so
        # concurrent uploads for the same tenant cannot double-spend the ledger.
        with transaction.atomic():
            try:
                reserve_workspace_bytes(workspace.id, image_file.size)
            except QuotaExceededError:
                raise ValidationError("Storage quota exceeded. Please upgrade your subscription.")

            photo = serializer.save(
                file_size_bytes=image_file.size,
                original_filename=safe_filename,
                is_processed=False,
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
        release_workspace_bytes(workspace.id, file_size)
        instance.delete()
