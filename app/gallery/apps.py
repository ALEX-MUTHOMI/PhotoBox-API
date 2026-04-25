import logging
from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)

class GalleryConfig(AppConfig):
    name = 'gallery'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        """
        Validate required R2 credentials at startup.

        PRODUCTION GRADE (Graceful Degradation):
        Instead of a hard crash (ImproperlyConfigured) that takes down the 
        entire Django server and ALL Celery workers, we log a critical warning. 
        This ensures that if R2 keys are missing, expire, or are omitted in a CI/CD 
        test pipeline, unrelated queues (like billing webhooks or emails) stay alive. 
        R2-dependent tasks will fail safely at the point of execution.
        """
        required = [
            'CLOUDFLARE_R2_ENDPOINT',
            'CLOUDFLARE_R2_BUCKET_NAME',
            'CLOUDFLARE_ACCESS_KEY_ID',
            'CLOUDFLARE_SECRET_ACCESS_KEY',
        ]
        
        missing = [k for k in required if not getattr(settings, k, None)]
        if missing:
            logger.critical(
                f"R2 storage is misconfigured. Missing settings: {missing}. "
                f"Gallery uploads will safely fail until these are provided in the environment."
            )