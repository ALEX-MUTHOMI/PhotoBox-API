from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.conf import settings


class GalleryConfig(AppConfig):
    name = 'gallery'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        """
        Validate required R2 credentials at startup, not at first request.

        PRODUCTION GRADE:
        No test bypasses or OS-level hacks here. The application fiercely 
        demands credentials. If this runs in CI/CD, the test runner MUST 
        provide placeholder values via test_settings.py or a .env.test file.
        """
        required = [
            'CLOUDFLARE_R2_ENDPOINT',
            'CLOUDFLARE_R2_BUCKET_NAME',
            'CLOUDFLARE_ACCESS_KEY_ID',
            'CLOUDFLARE_SECRET_ACCESS_KEY',
        ]
        
        missing = [k for k in required if not getattr(settings, k, None)]
        if missing:
            raise ImproperlyConfigured(
                f"R2 storage is misconfigured. Missing settings: {missing}. "
                f"Set them as environment variables before starting the server."
            )