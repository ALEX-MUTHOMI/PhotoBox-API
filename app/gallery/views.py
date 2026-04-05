"""
Views for the Gallery API.
"""
from django.db.models import Sum # NEW: Required for the Quota Shield
from rest_framework import viewsets, parsers
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from core.models import Gallery, Workspace, Image
from gallery import serializers

# NEW: Enterprise Image Inspection
from PIL import Image as PILImage
from PIL import UnidentifiedImageError


# ==========================================
# 1. GALLERY MANAGEMENT
# ==========================================

class GalleryViewSet(viewsets.ModelViewSet):
    """Viewset for managing gallery resources."""
    serializer_class = serializers.GallerySerializer
    queryset = Gallery.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Retrieve galleries limited to the authenticated user's workspace."""
        # SAAS UPGRADE: Enforce tenant isolation AND hide soft-deleted items
        return self.queryset.filter(
            workspace__user=self.request.user,
            is_deleted=False
        ).order_by('-created_at')

    def perform_create(self, serializer):
        """Create a new gallery associated with the user's workspace."""
        # SECURITY: Prevent 500 Server Crashes if a user hits this endpoint without a workspace
        try:
            workspace = Workspace.objects.get(user=self.request.user)
        except Workspace.DoesNotExist:
            raise ValidationError("A workspace must be initialized before creating galleries.")

        serializer.save(workspace=workspace)

    def perform_destroy(self, instance):
        """
        SAAS SECURITY OVERRIDE:
        Intercept the standard DELETE request and replace it with a Soft Delete.
        This sends it to the 7-day trash can instead of nuking the database.
        """
        instance.is_deleted = True
        instance.save()


# ==========================================
# 2. IMAGE UPLOAD & HANDLING
# ==========================================

class ImageViewSet(viewsets.ModelViewSet):
    """Viewset for uploading and managing image files."""
    serializer_class = serializers.ImageSerializer
    queryset = Image.objects.all()

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    # CRITICAL: Django must know how to parse binary multipart file uploads natively
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get_queryset(self):
        """Retrieve active images, enforce the soft-delete cascade, and allow filtering."""

        # 1. THE CASCADE RULE:
        # Only return the image if BOTH the image AND its parent gallery are active.
        queryset = self.queryset.filter(
            gallery__workspace__user=self.request.user,
            is_deleted=False,
            gallery__is_deleted=False
        )

        # 2. THE REACT OPTIMIZATION RULE:
        # If the React frontend asks for a specific gallery (e.g., ?gallery=123), filter it.
        gallery_id = self.request.query_params.get('gallery')
        if gallery_id:
            queryset = queryset.filter(gallery_id=gallery_id)

        return queryset.order_by('order', '-created_at')

    def perform_create(self, serializer):
        """
        SECURITY: Prevent Cross-Tenant Hijacking, Quota Abuse, and Malware Spoofing.
        """
        gallery = serializer.validated_data['gallery']

        # 1. CROSS-TENANT HIJACKING SHIELD
        if gallery.workspace.user != self.request.user:
            raise PermissionDenied("You do not have permission to upload to this gallery.")

        # 2. GHOST INJECTION SHIELD
        # Prevent hackers from uploading images to a gallery that is currently in the Trash Can.
        if gallery.is_deleted:
            raise ValidationError("You cannot upload images to a deleted gallery. Restore it first.")

        image_file = self.request.FILES.get('image')
        if image_file:
            # 3. QUOTA SHIELD (Denial of Wallet Protection)
            # Calculate the total bytes the user is currently using across all active images
            current_usage_dict = Image.objects.filter(
                gallery__workspace__user=self.request.user,
                is_deleted=False
            ).aggregate(total_bytes=Sum('file_size_bytes'))

            # If they have no images, it returns None, so default to 0
            current_usage = current_usage_dict['total_bytes'] or 0

            # Get the user's limit from their profile (e.g., 5GB)
            limit_gb = self.request.user.storage_limit_gb
            limit_bytes = limit_gb * 1024 * 1024 * 1024

            if (current_usage + image_file.size) > limit_bytes:
                raise ValidationError(f"Storage Quota Exceeded. You have hit your {limit_gb}GB limit. Please upgrade your subscription.")

            # 4. MALWARE & DECOMPRESSION BOMB SHIELD
            # Check Absolute File Size (25MB Limit per file)
            MAX_FILE_SIZE_MB = 25
            if image_file.size > (MAX_FILE_SIZE_MB * 1024 * 1024):
                raise ValidationError(f"Payload too large. Maximum size is {MAX_FILE_SIZE_MB}MB.")

            # CRYPTOGRAPHIC FILE INSPECTION: Do not trust the HTTP Content-Type header.
            try:
                with PILImage.open(image_file) as img:
                    img.verify() # Verifies it is mathematically an image without decoding it

                    # PIXEL BOMB SHIELD: Check actual dimensions, not just file size
                    MAX_PIXELS = 10000 * 10000 # 100 Megapixels max
                    if img.width * img.height > MAX_PIXELS:
                         raise ValidationError("Image dimensions are dangerously large (Potential Pixel Bomb).")

                    # Verify Format
                    if img.format not in ['JPEG', 'PNG', 'WEBP']:
                         raise ValidationError("Invalid file. Only actual JPEG, PNG, and WEBP files are permitted.")

            except UnidentifiedImageError:
                raise ValidationError("Malware Shield: The uploaded file is disguised or corrupted.")

            # Reset the file pointer so Django can actually save it after Pillow read it
            image_file.seek(0)

            # 5. EXECUTE THE VAULT SAVE
            # Save the exact byte count to the database so our Quota Shield math works next time
            serializer.save(file_size_bytes=image_file.size)
        else:
            # If no file was provided (e.g., just updating text fields)
            serializer.save()

    def perform_destroy(self, instance):
        """
        SAAS SECURITY OVERRIDE:
        Intercept standard DELETE to enforce the Trash Can recovery window.
        """
        instance.is_deleted = True
        instance.save()
