import logging
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.db import transaction
from botocore.exceptions import ClientError
from gallery.models import MediaAsset, Workspace
from .views import get_r2_client
from django.conf import settings

logger = logging.getLogger(__name__)

@shared_task
def reap_abandoned_uploads():
    """
    THE FIX: The Reaper Task with Phantom Upload Defense.
    Finds PENDING assets older than 24 hours.
    CRITICALLY: Verifies via Cloudflare R2 if the file actually exists 
    before refunding the workspace quota.
    """
    cutoff_time = timezone.now() - timedelta(hours=24)
    
    # 1. Look for abandoned tickets
    abandoned_assets = MediaAsset.objects.filter(
        status='PENDING',
        uploaded_at__lt=cutoff_time
    ).select_related('scene__event__workspace')
    
    if not abandoned_assets.exists():
        return "No abandoned assets to reap."

    r2_client = get_r2_client()
    reaped_count = 0
    phantom_count = 0

    for asset in abandoned_assets:
        workspace = asset.scene.event.workspace
        
        # 2. THE PHANTOM EXORCISM: Head check R2 physically
        file_physically_exists = False
        try:
            r2_client.head_object(
                Bucket=getattr(settings, 'CLOUDFLARE_R2_BUCKET_NAME', 'test-bucket'),
                Key=asset.r2_object_key
            )
            file_physically_exists = True
        except ClientError as e:
            # 404 means it truly wasn't uploaded.
            if e.response['Error']['Code'] == '404':
                file_physically_exists = False
            else:
                # Other errors (throttle, auth), skip and try next time.
                logger.error(f"Reaper R2 API Error for {asset.id}: {e}")
                continue
                
        with transaction.atomic():
            # Lock the workspace row to safely adjust quota
            locked_workspace = Workspace.objects.select_for_update().get(id=workspace.id)
            
            if file_physically_exists:
                # PHANTOM UPLOAD DETECTED!
                # Hacker uploaded the file but blocked the webhook to keep it 'PENDING'.
                # We DO NOT refund quota. We mark it as QUARANTINED (or UPLOADED).
                logger.critical(f"PHANTOM UPLOAD DETECTED: Asset {asset.id} exists in R2 but webhook was suppressed!")
                phantom_count += 1
                asset.status = 'QUARANTINED' 
                asset.save(update_fields=['status'])
            else:
                # LEGITIMATE ABANDONMENT
                # User requested ticket but never uploaded. Safe to refund.
                reaped_count += 1
                locked_workspace.storage_used_bytes -= asset.file_size_bytes
                # Ensure we don't go below 0 due to unexpected math
                locked_workspace.storage_used_bytes = max(0, locked_workspace.storage_used_bytes)
                locked_workspace.save(update_fields=['storage_used_bytes'])
                
                asset.status = 'FAILED'
                asset.save(update_fields=['status'])
                
    return f"Reaper finished. Reaped: {reaped_count}. Phantoms Caught: {phantom_count}."
