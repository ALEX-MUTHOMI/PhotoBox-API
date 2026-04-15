import uuid
import logging
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction, IntegrityError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import UserRateThrottle

from .serializers import BulkManifestSerializer, MAX_IMAGE_SIZE_BYTES, MAX_VIDEO_SIZE_BYTES
from gallery.models import Scene, MediaAsset, Workspace

logger = logging.getLogger(__name__)

# PERFORMANCE OPTIMIZATION: Singleton client prevents CPU bottlenecks
# from re-reading AWS credentials on every single HTTP request.
r2_client_instance = None

def get_r2_client():
    """Singleton-style client generation for Cloudflare R2."""
    global r2_client_instance
    if r2_client_instance is None:
        r2_client_instance = boto3.client(
            's3',
            endpoint_url=settings.CLOUDFLARE_R2_ENDPOINT,
            aws_access_key_id=settings.CLOUDFLARE_ACCESS_KEY_ID,
            aws_secret_access_key=settings.CLOUDFLARE_SECRET_ACCESS_KEY,
            region_name='auto'
        )
    return r2_client_instance

class BulkIngestionView(APIView):
    """
    THE VANGUARD GATEWAY.
    Processes the manifest, secures the DB state, checks economic quotas,
    and mints cryptographically bound R2 upload tickets.
    """
    permission_classes = [IsAuthenticated]
    throttle_classes = [UserRateThrottle] # L7 DoS Connection Pool Defense

    def post(self, request, *args, **kwargs):
        serializer = BulkManifestSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated_data = serializer.validated_data
        scene_id = validated_data['scene_id']
        files = validated_data['files']
        user = request.user

        total_incoming_bytes = sum(f['file_size'] for f in files)
        response_payload = []
        db_assets_to_create = []

        try:
            r2_client = get_r2_client()

            # THE ATOMIC VAULT: All DB operations succeed together, or roll back together.
            with transaction.atomic():

                # 1. THE ECONOMIC LEDGER
                # select_for_update() locks the row against concurrent race conditions
                workspace = Workspace.objects.select_for_update().get(user=user)

                if workspace.storage_used_bytes + total_incoming_bytes > workspace.storage_limit_bytes:
                    return Response(
                        {"detail": "Storage quota exceeded. Please upgrade your plan."},
                        status=status.HTTP_402_PAYMENT_REQUIRED
                    )

                # Pre-allocate storage.
                # Note: A background Celery "Reaper" task will eventually refund this if the upload is abandoned.
                workspace.storage_used_bytes += total_incoming_bytes
                workspace.save(update_fields=['storage_used_bytes'])

                # 2. THE CLOUD TICKET FACTORY
                # Handled inside the atomic block to catch split-second deletes
                scene = Scene.objects.get(id=scene_id)

                for file_item in files:
                    sanitized_name = file_item['sanitized_filename']
                    media_type = file_item['media_type']
                    client_ref = file_item['client_reference_id']
                    file_size = file_item['file_size']

                    unique_file_id = uuid.uuid4()

                    # SECURITY: Strict Tenant Path Isolation
                    object_key = f"raw/tenant_{user.id}/scene_{scene.id}/{unique_file_id}_{sanitized_name}"

                    if media_type == 'IMAGE':
                        max_bytes = MAX_IMAGE_SIZE_BYTES
                        mime_prefix = "image/"
                    else:
                        max_bytes = MAX_VIDEO_SIZE_BYTES
                        mime_prefix = "video/"

                    # SECURITY: Size limits AND MIME-Sniffing Defense
                    conditions = [
                        ["content-length-range", 1, max_bytes],
                        ["starts-with", "$key", object_key],
                        ["starts-with", "$Content-Type", mime_prefix]
                    ]

                    # Local Cryptographic Ticket Generation (Microseconds, safe inside DB lock)
                    presigned_data = r2_client.generate_presigned_post(
                        Bucket=settings.CLOUDFLARE_R2_BUCKET_NAME,
                        Key=object_key,
                        Fields={
                            "x-amz-meta-media-type": media_type,
                            "x-amz-meta-client-ref": client_ref,
                            # Forces the React client to send a matching, safe MIME type
                            "Content-Type": f"{mime_prefix}*"
                        },
                        Conditions=conditions,
                        ExpiresIn=300
                    )

                    # Stage the model in memory
                    db_assets_to_create.append(
                        MediaAsset(
                            id=unique_file_id,
                            scene=scene,
                            original_filename=sanitized_name,
                            r2_object_key=object_key,
                            media_type=media_type,
                            file_size_bytes=file_size,
                            status='PENDING' # Reaper Task tracks this status
                        )
                    )

                    response_payload.append({
                        "client_reference_id": client_ref,
                        "post_url": presigned_data['url'],
                        "post_fields": presigned_data['fields']
                    })

                # PERFORMANCE: O(1) Scalability Insert
                MediaAsset.objects.bulk_create(db_assets_to_create)

        except Workspace.DoesNotExist:
            return Response({"detail": "Workspace not found."}, status=status.HTTP_400_BAD_REQUEST)

        except Scene.DoesNotExist:
            # PostgreSQL automatically refunds the storage quota.
            logger.warning(f"Scene {scene_id} was deleted mid-transaction by User {user.id}.")
            return Response(
                {"detail": "The target scene no longer exists. Upload aborted."},
                status=status.HTTP_404_NOT_FOUND
            )

        except (BotoCoreError, ClientError) as e:
            # PostgreSQL automatically refunds the storage quota.
            logger.error(f"Cloudflare R2 API Failure: {str(e)}")
            return Response(
                {"detail": "Storage provider is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        except IntegrityError as e:
            # PostgreSQL automatically refunds the storage quota.
            logger.critical(f"Database Integrity Error during bulk insert: {str(e)}")
            return Response(
                {"detail": "Internal database error. Request aborted."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # 3. Deliver the payload back to React
        return Response({"upload_tickets": response_payload}, status=status.HTTP_201_CREATED)
