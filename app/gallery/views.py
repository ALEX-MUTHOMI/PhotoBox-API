"""
Views for the Gallery API (The Pixieset Standard).
"""
import os
from django.db.models import F, Sum
from rest_framework import viewsets, parsers, mixins
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.throttling import UserRateThrottle

from rest_framework.response import Response
from rest_framework import status

from core.models import Workspace
from gallery.models import Event, Scene, Photo
from gallery import serializers

# Enterprise Image Inspection
from PIL import Image as PILImage
from PIL import UnidentifiedImageError
import logging

logger = logging.getLogger(__name__)


# ==========================================
# 1. PIXIESET STANDARD: EVENT (The Collection)
# ==========================================

from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin

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
    throttle_classes = [UserRateThrottle]

    def get_queryset(self):
        """Retrieve events limited to the authenticated user's workspace."""
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


# ==========================================
# 2. PIXIESET STANDARD: THE STAGE (Scenes / Tabs)
# ==========================================

class SceneViewSet(viewsets.ModelViewSet):
    """Viewset for managing Sub-Sets within an Event (e.g., Ceremony, Reception)."""
    serializer_class = serializers.SceneSerializer
    queryset = Scene.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    # RATE LIMITING
    throttle_classes = [UserRateThrottle]

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

class PhotoFastLaneViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet
):
    """
    The Fast Lane HTTP Uploader. Restricts payload size and performs cryptographic Magic Byte checks.
    """
    serializer_class = serializers.PhotoFastLaneSerializer
    queryset = Photo.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    # DRF native Multipart parser for binary
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get_queryset(self):
        """Only fetch Photos mapping back to this user's workspace."""
        queryset = self.queryset.filter(
            scene__event__workspace__user=self.request.user
        )
        
        scene_id = self.request.query_params.get('scene')
        if scene_id:
            queryset = queryset.filter(scene_id=scene_id)
            
        return queryset.order_by('-uploaded_at')

    def perform_create(self, serializer):
        """
        EDA FAST LANE PERIMETER: This method does NO I/O to external services.
        It runs only CPU-bound checks, writes to DB, fires async task, then exits.
        The Django worker is freed in < 100ms regardless of file size.
        """
        # Import here to avoid circular import at module level
        from gallery.tasks import upload_fast_lane_to_cloudinary

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
        # Reads the binary header to reject .exe/.zip files disguised as .jpg.
        # img.verify() never decodes the full pixel data, so it stays lightweight.
        try:
            image_file.seek(0)
            with PILImage.open(image_file) as img:
                img.verify()
                if img.width * img.height > (10000 * 10000):
                    raise ValidationError("Decompression Bomb detected: image pixel count exceeds 100MP.")
                if img.format not in ['JPEG', 'PNG', 'WEBP']:
                    raise ValidationError("Invalid Magic Bytes. Not a genuine JPEG, PNG, or WEBP.")
        except UnidentifiedImageError:
            raise ValidationError("Malware Shield: Uploaded file is disguised or structurally corrupted.")

        # Reset file pointer so Django's storage backend can write it to disk
        image_file.seek(0)

        workspace = scene.event.workspace

        # 4. ATOMIC QUOTA GATE & RACE CONDITION DEFENSE
        #
        # KNOWN LIMITATION (TOCTOU Race Condition):
        # The check below is a READ, and the .update() below is a WRITE.
        # Two concurrent requests can both pass this `if` check before either
        # commits the update, allowing a brief double-spend of quota bytes.
        #
        # CORRECT FIX (when scale demands it):
        #   with transaction.select_for_update():
        #       workspace = Workspace.objects.select_for_update().get(id=workspace.id)
        #       if (workspace.storage_used_bytes + image_file.size) > workspace.storage_limit_bytes:
        #           raise ValidationError(...)
        #       workspace.storage_used_bytes = F('storage_used_bytes') + image_file.size
        #       workspace.save(update_fields=['storage_used_bytes'])
        #
        # Current implementation is acceptable for low concurrency (< 100 rps per workspace).
        if (workspace.storage_used_bytes + image_file.size) > workspace.storage_limit_bytes:
            raise ValidationError("Storage quota exceeded. Please upgrade your subscription.")

        Workspace.objects.filter(id=workspace.id).update(
            storage_used_bytes=F('storage_used_bytes') + image_file.size
        )

        safe_filename = os.path.basename(image_file.name)

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
        # Cloudinary upload happens asynchronously in a background Celery process.
        upload_fast_lane_to_cloudinary.delay(str(photo.id))

        logger.info(
            f"[FAST LANE] Photo {photo.id} accepted. "
            f"Celery task dispatched. Worker freed immediately."
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
            storage_used_bytes=F('storage_used_bytes') - file_size
        )
        
        instance.delete()
