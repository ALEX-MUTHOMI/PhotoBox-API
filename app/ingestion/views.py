import uuid
import logging
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import transaction, IntegrityError
from django.db.utils import OperationalError
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
    # THE FIX: Thread-safe connection pool using raw boto3 Session
    # Prevents crossing credential paths across multiple Gunicorn/Uvicorn threads.
    session = boto3.Session(
        aws_access_key_id=getattr(settings, 'CLOUDFLARE_ACCESS_KEY_ID', 'test-key'),
        aws_secret_access_key=getattr(settings, 'CLOUDFLARE_SECRET_ACCESS_KEY', 'test-secret'),
        region_name='auto'
    )
    return session.client(
        's3',
        endpoint_url=getattr(settings, 'CLOUDFLARE_R2_ENDPOINT', 'https://test.r2.cloudflarestorage.com')
    )

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
            # 1. THE CLOUD TICKET FACTORY (Moved OUTSIDE DB Lock to prevent Connection Exhaustion)
            # Mathematical hashes take CPU time. We don't hold Postgres row locks while doing this.
            r2_client = get_r2_client()
            
            try:
                scene = Scene.objects.get(id=scene_id)
            except Scene.DoesNotExist:
                return Response(
                    {"detail": "The target scene no longer exists. Upload aborted."},
                    status=status.HTTP_404_NOT_FOUND
                )

            for file_item in files:
                sanitized_name = file_item['sanitized_filename']
                media_type = file_item['media_type']
                client_ref = file_item['client_reference_id']
                file_size = file_item['file_size']

                unique_file_id = uuid.uuid4()
                object_key = f"raw/tenant_{user.id}/scene_{scene.id}/{unique_file_id}_{sanitized_name}"

                max_bytes = MAX_IMAGE_SIZE_BYTES if media_type == 'IMAGE' else MAX_VIDEO_SIZE_BYTES
                mime_prefix = "image/" if media_type == 'IMAGE' else "video/"

                conditions = [
                    ["content-length-range", 1, max_bytes],
                    ["starts-with", "$key", object_key],
                    ["starts-with", "$Content-Type", mime_prefix]
                ]

                # Cryptographic Ticket Generation (CPU Intense, NO locks held!)
                presigned_data = r2_client.generate_presigned_post(
                    Bucket=getattr(settings, 'CLOUDFLARE_R2_BUCKET_NAME', 'test-bucket'),
                    Key=object_key,
                    Fields={
                        "x-amz-meta-media-type": media_type,
                        "x-amz-meta-client-ref": client_ref,
                        "Content-Type": f"{mime_prefix}*"
                    },
                    Conditions=conditions,
                    ExpiresIn=300
                )

                db_assets_to_create.append(
                    MediaAsset(
                        id=unique_file_id,
                        scene=scene,
                        original_filename=sanitized_name,
                        r2_object_key=object_key,
                        media_type=media_type,
                        file_size_bytes=file_size,
                        status='PENDING' 
                    )
                )

                response_payload.append({
                    "client_reference_id": client_ref,
                    "post_url": presigned_data['url'],
                    "post_fields": presigned_data['fields']
                })

            # 2. THE ATOMIC VAULT & ECONOMIC LEDGER
            # Now we lock the database. It is incredibly fast because math is already done.
            with transaction.atomic():
                # SECURITY: nowait=True prevents slowloris DoS attacks on DB connections.
                # If someone else is modifying this tenant's quota, we throw 409 immediately.
                workspace = Workspace.objects.select_for_update(nowait=True).get(user=user)

                if workspace.storage_used_bytes + total_incoming_bytes > workspace.storage_limit_bytes:
                    return Response(
                        {"detail": "Storage quota exceeded. Please upgrade your plan."},
                        status=status.HTTP_402_PAYMENT_REQUIRED
                    )

                workspace.storage_used_bytes += total_incoming_bytes
                workspace.save(update_fields=['storage_used_bytes'])
                
                # O(1) Scalability Insert
                MediaAsset.objects.bulk_create(db_assets_to_create)

        except Workspace.DoesNotExist:
            return Response({"detail": "Workspace not found."}, status=status.HTTP_400_BAD_REQUEST)

        except OperationalError:
            # SECURITY FIX: Caught the nowait=True lock collision.
            logger.warning(f"L7 Concurrency Defense: DB lock collision handled for User {user.id}")
            return Response(
                {"detail": "Workspace is currently processing another bulk upload. Please try again in a few seconds."},
                status=status.HTTP_409_CONFLICT
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
