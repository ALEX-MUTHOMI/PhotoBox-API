import os
import re
import logging
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from gallery.models import Scene

logger = logging.getLogger(__name__)

# --- INFRASTRUCTURE LIMITS ---
MAX_FILES_PER_BATCH = 2000
MAX_IMAGE_SIZE_BYTES = 50 * 1024 * 1024         # 50 MB
MAX_VIDEO_SIZE_BYTES = 5 * 1024 * 1024 * 1024   # 5 GB

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm'}
VALID_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.tiff', '.cr2', '.nef', '.arw'}

class ManifestFileItemSerializer(serializers.Serializer):
    """THE BARE METAL SANITIZER: Validates individual file payloads."""
    filename = serializers.CharField(max_length=255)
    file_size = serializers.IntegerField(min_value=1) # Defeats negative math exploits
    client_reference_id = serializers.CharField(max_length=255)

    def validate(self, attrs):
        raw_filename = attrs.get('filename', '')
        file_size = attrs.get('file_size', 0)

        # 1. Null Byte Shield (Protects PostgreSQL C-bindings)
        if '\x00' in raw_filename:
            raise ValidationError({"filename": "FATAL: Null byte detected."})

        # 2. XSS Stripping & URL-Safe Slugification
        base_name = os.path.basename(raw_filename)
        no_scripts = re.sub(r'<[^>]+>', '', base_name)
        sanitized_name = re.sub(r'[^\w\s.-]', '', no_scripts).strip()
        sanitized_name = re.sub(r'[\s]+', '_', sanitized_name)

        # 3. Media Type Routing & Spoof Defense
        name_part, ext_part = os.path.splitext(sanitized_name)
        ext_part = ext_part.lower()

        # THE FIX 1: Strip leading dots to completely eradicate Unix hidden-file exploits
        name_part = name_part.lstrip('.')
        if not name_part:
            name_part = "unnamed_asset"

        if ext_part in VIDEO_EXTENSIONS:
            media_type = 'VIDEO'
        elif ext_part in VALID_IMAGE_EXTENSIONS:
            media_type = 'IMAGE'
        else:
            # Neutralize unknown/executable extensions by forcing them to .jpg
            media_type = 'IMAGE'
            ext_part = '.jpg'

        # Reconstruct the final, safely sanitized filename
        sanitized_name = f"{name_part}{ext_part}"

        # 4. Asymmetric MIME Bomb Defense (OOM Protection)
        if media_type == 'IMAGE' and file_size > MAX_IMAGE_SIZE_BYTES:
            raise ValidationError({"file_size": f"Exceeds max image size ({MAX_IMAGE_SIZE_BYTES} bytes)."})
        if media_type == 'VIDEO' and file_size > MAX_VIDEO_SIZE_BYTES:
            raise ValidationError({"file_size": f"Exceeds max video size ({MAX_VIDEO_SIZE_BYTES} bytes)."})

        # Inject calculated values for the View to use
        attrs['sanitized_filename'] = sanitized_name
        attrs['media_type'] = media_type

        return attrs


class BulkManifestSerializer(serializers.Serializer):
    """THE BATCH ORCHESTRATOR: Validates array limits, uniqueness, and Row-Level Tenant Isolation."""
    scene_id = serializers.UUIDField()
    files = ManifestFileItemSerializer(many=True)

    def validate_files(self, value):
        if not value:
            raise ValidationError("The files array cannot be empty.")
        if len(value) > MAX_FILES_PER_BATCH:
            raise ValidationError(f"Maximum of {MAX_FILES_PER_BATCH} files allowed per request.")

        # THE FIX 2: State-Poisoning Defense (Ensure absolute uniqueness of client references)
        client_refs = [item['client_reference_id'] for item in value]
        if len(client_refs) != len(set(client_refs)):
            raise ValidationError("Duplicate client_reference_id detected in the payload array.")

        return value

    def validate(self, attrs):
        request = self.context.get('request')
        if not request or not getattr(request, 'user', None):
            raise ValidationError("Critical context missing: Request or User not provided.")

        scene_id = attrs.get('scene_id')

        # TENANT ISOLATION: Ensure the user owns this Scene
        # This executes in O(1) time per batch, preventing database starvation.
        tenant_owns_scene = Scene.objects.filter(
            id=scene_id,
            event__workspace__user=request.user
        ).exists()

        if not tenant_owns_scene:
            logger.warning(
                f"UNAUTHORIZED TENANT ACCESS ATTEMPT: User ID {request.user.id} "
                f"attempted to access Scene ID {scene_id}."
            )
            # Generic 400 prevents hackers from enumerating valid UUIDs
            raise ValidationError({"scene_id": "Invalid scene_id or unauthorized access."})

        return attrs
