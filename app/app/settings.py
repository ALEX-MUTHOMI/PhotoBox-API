"""
Django settings for photobox-api.

ARCHITECTURE: Single-file settings with an explicit test-mode block.
No separate settings files, no inheritance chains — just one source of truth
with clearly labeled overrides at the bottom.
"""

import os
import sys
import mimetypes
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured


# ============================================================
# 0. PATH RESOLUTION
# ============================================================
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from the project root (one level above manage.py)
load_dotenv(dotenv_path=BASE_DIR / '.env')


# ============================================================
# 1. FAIL-FAST PERIMETER  —  halt before serving a broken app
# ============================================================
def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _detect_test_mode() -> bool:
    """
    Detect both Django's built-in test runner and pytest/pytest-django.

    The previous exact-token check only matched `manage.py test`, which meant
    `pytest gallery/tests/...` skipped the in-memory storage and eager Celery
    safeguards. That made the suite behave differently in Docker/CI vs local
    Django test runs.
    """
    if _env_flag("TESTING"):
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True

    argv = [str(arg).lower() for arg in sys.argv]
    if any(arg in {"test", "pytest", "py.test"} for arg in argv):
        return True

    return any("/tests/" in arg or "\\tests\\" in arg for arg in argv)


# Skip credential checks when Django is only loading for the test runner.
# The test block at the bottom injects safe stubs for all external services.
_IS_TEST = _detect_test_mode()
TESTING = _IS_TEST

if not _IS_TEST:
    _missing = []

    if not os.environ.get('DB_NAME'):
        _missing.append('DB_NAME')
    if not os.environ.get('DB_PASS'):
        _missing.append('DB_PASS')
    if not os.environ.get('SECRET_KEY'):
        _missing.append('SECRET_KEY')

    if _missing:
        raise ImproperlyConfigured(
            f"CRITICAL: Required environment variables are missing: {_missing}. "
            f"Check your .env file."
        )


# ============================================================
# 2. CORE DJANGO SETTINGS
# ============================================================
SECRET_KEY = os.environ.get('SECRET_KEY', 'INSECURE-LOCAL-DEV-KEY-DO-NOT-USE-IN-PROD')
DEBUG      = bool(int(os.environ.get('DEBUG', 0)))

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        'ALLOWED_HOSTS',
        '127.0.0.1,localhost,.ngrok.app,.ngrok-free.app'
    ).split(',')
    if host.strip()
]

FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:3000')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL    = 'core.User'
SITE_ID            = 1


# ============================================================
# 3. PRODUCTION HTTPS SECURITY HEADERS
#    Only activated when DEBUG=False so local dev isn't broken.
# ============================================================
if not DEBUG:
    SECURE_SSL_REDIRECT            = True
    SECURE_HSTS_SECONDS            = 31536000   # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD            = True
    SECURE_CONTENT_TYPE_NOSNIFF    = True
    SECURE_BROWSER_XSS_FILTER      = True
    SESSION_COOKIE_SECURE          = True
    CSRF_COOKIE_SECURE             = True
    X_FRAME_OPTIONS                = 'DENY'


# ============================================================
# 4. INSTALLED APPS
# ============================================================
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',

    # Third-party
    'corsheaders',
    'rest_framework',
    'rest_framework.authtoken',
    'rest_framework_simplejwt.token_blacklist',   # Required by ROTATE_REFRESH_TOKENS
    'drf_spectacular',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'dj_rest_auth',
    'dj_rest_auth.registration',

    # First-party
    'core',
    'user',
    'gallery',
    'billing',
    'checkout',
    'ingestion',
    'webhooks',
]


# ============================================================
# 5. MIDDLEWARE
# ============================================================
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',          # Must precede CommonMiddleware
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


# ============================================================
# 6. URLS / WSGI / TEMPLATES
# ============================================================
ROOT_URLCONF   = 'app.urls'
WSGI_APPLICATION = 'app.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# ============================================================
# 7. DATABASE
# ============================================================
DATABASES = {
    'default': {
        'ENGINE':   'django.db.backends.postgresql',
        'HOST':     os.environ.get('DB_HOST'),
        'NAME':     os.environ.get('DB_NAME'),
        'USER':     os.environ.get('DB_USER'),
        'PASSWORD': os.environ.get('DB_PASS'),
        'CONN_MAX_AGE': 60,   # Persistent connections: reduces per-request TCP overhead
    }
}


# ============================================================
# 8. PASSWORD VALIDATION & HASHING
# ============================================================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',      # Primary: gold standard
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',      # Fallback for legacy hashes
    'django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
]


# ============================================================
# 9. INTERNATIONALISATION
# ============================================================
LANGUAGE_CODE = 'en-us'
TIME_ZONE     = 'UTC'
USE_I18N      = True
USE_L10N      = True
USE_TZ        = True


# ============================================================
# 10. STATIC & MEDIA FILES
# ============================================================
# Fix for Windows where IIS/dev-server misidentifies .css as text/plain
mimetypes.add_type("text/css", ".css", True)

STATIC_URL  = '/static/'
STATIC_ROOT = BASE_DIR / 'static_root'
STATIC_DIR = BASE_DIR / 'static'
STATICFILES_DIRS = [STATIC_DIR] if STATIC_DIR.exists() else []

MEDIA_URL  = '/media/'
MEDIA_ROOT = BASE_DIR / 'media_root'


# ============================================================
# 11. CACHING  (LocMem for dev/test — swap for Redis in prod)
# ============================================================
_REDIS_URL = os.environ.get('REDIS_URL', os.environ.get('CELERY_BROKER_URL', ''))

if _REDIS_URL and not _IS_TEST:
    CACHES = {
        'default': {
            'BACKEND':  'django.core.cache.backends.redis.RedisCache',
            'LOCATION': _REDIS_URL,
        }
    }
else:
    # Local dev and test runner both get an in-process LocMem cache.
    # Throttle tests can count requests correctly; no Redis required.
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }


# ============================================================
# 12. DJANGO REST FRAMEWORK
# ============================================================
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ), # <--- Notice the tuple ends here
    
    # Move EXCEPTION_HANDLER out here, at the dictionary level
    'EXCEPTION_HANDLER': 'user.exceptions.custom_exception_handler',
    
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '5/minute',
        'user': '1000/day',
        'fast_lane_upload': '30/minute',
        'heavy_lane_ticket': '10/minute',
    },
}

SPECTACULAR_SETTINGS = {
    'COMPONENT_SPLIT_REQUEST': True,
}


# ============================================================
# 13. JWT AUTHENTICATION
# ============================================================
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME':  timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS':  True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
    # Explicitly set the signing key so it is never accidentally derived
    # from a too-short SECRET_KEY in test/CI environments.
    'SIGNING_KEY': os.environ.get('JWT_SIGNING_KEY', SECRET_KEY),
}


# ============================================================
# 14. ALLAUTH / DJ-REST-AUTH: IDENTITY FEDERATION
# ============================================================
REST_USE_JWT = True
JWT_AUTH_COOKIE         = 'access'
JWT_AUTH_REFRESH_COOKIE = 'refresh'

ACCOUNT_USER_MODEL_USERNAME_FIELD  = None
ACCOUNT_EMAIL_REQUIRED             = True
ACCOUNT_USERNAME_REQUIRED          = False
ACCOUNT_AUTHENTICATION_METHOD      = 'email'

# Prevent blind social account takeovers:
# Google login matching an existing email is blocked, not auto-linked.
SOCIALACCOUNT_EMAIL_AUTHENTICATION             = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = False
SOCIALACCOUNT_ADAPTER = 'user.adapters.HardenedSocialAccountAdapter'

REST_AUTH_REGISTER_SERIALIZERS = {
    'REGISTER_SERIALIZER': 'user.serializers.UserSerializer',
}


# ============================================================
# 15. CORS
# ============================================================
CORS_ALLOW_ALL_ORIGINS = False   # NEVER True in production

CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        'CORS_ALLOWED_ORIGINS',
        'http://localhost:3000,http://127.0.0.1:3000'
    ).split(',')
    if origin.strip()
]

CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]


# ============================================================
# 16. CLOUDFLARE R2 STORAGE
# ============================================================
# IAM credentials MUST be scoped to s3:PutObject + s3:GetObject only.
# Never grant s3:DeleteObject, s3:ListBucket, or s3:PutBucketPolicy.
CLOUDFLARE_R2_ENDPOINT          = os.environ.get('CLOUDFLARE_R2_ENDPOINT', '')
CLOUDFLARE_R2_BUCKET_NAME       = os.environ.get('CLOUDFLARE_R2_BUCKET_NAME', '')
CLOUDFLARE_R2_DOMAIN            = os.environ.get('CLOUDFLARE_R2_DOMAIN', '')
CLOUDFLARE_ACCESS_KEY_ID        = os.environ.get('CLOUDFLARE_ACCESS_KEY_ID', '')
CLOUDFLARE_SECRET_ACCESS_KEY    = os.environ.get('CLOUDFLARE_SECRET_ACCESS_KEY', '')
CLOUDFLARE_WEBHOOK_SECRET       = os.environ.get('CLOUDFLARE_WEBHOOK_SECRET', '')


# ============================================================
# 17. CLOUDINARY (CDN FETCH PROXY — NO SDK UPLOADS)
# ============================================================
# Cloudinary acts as transform + cache layer only.
# It fetches originals from R2 on first request and serves WebP to clients.
CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', '')


# ============================================================
# 18. LEMON SQUEEZY BILLING
# ============================================================
LEMON_SQUEEZY_API_KEY   = os.environ.get('LEMON_SQUEEZY_API_KEY', '')
LEMON_SQUEEZY_STORE_ID  = os.environ.get('LEMON_SQUEEZY_STORE_ID', '')

# Dual-secret rotation: primary is active, secondary is retiring.
# Rotate by promoting secondary → primary and issuing a new secondary.
LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY   = os.environ.get('LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY', '')
LEMON_SQUEEZY_WEBHOOK_SECRET_SECONDARY = os.environ.get('LEMON_SQUEEZY_WEBHOOK_SECRET_SECONDARY', '')


# ============================================================
# 19. EMAIL
# ============================================================
EMAIL_BACKEND     = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST        = os.environ.get('EMAIL_HOST', 'smtp.sendgrid.net')
EMAIL_PORT        = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS     = True
EMAIL_HOST_USER   = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL  = os.environ.get('DEFAULT_FROM_EMAIL', 'PhotoBox <no-reply@photobox.app>')


# ============================================================
# 20. CELERY
# ============================================================
CELERY_BROKER_URL        = os.environ.get('CELERY_BROKER_URL', 'redis://127.0.0.1:6379/0')
CELERY_RESULT_BACKEND    = os.environ.get('CELERY_RESULT_BACKEND', 'redis://127.0.0.1:6379/0')
CELERY_ACCEPT_CONTENT    = ['json']
CELERY_TASK_SERIALIZER   = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE          = 'UTC'


# ============================================================
# 21. GALLERY BUSINESS RULES
# ============================================================
# TTL in days per subscription tier.  0 = unlimited (Enterprise).
# Enforced by the nightly Celery Beat purge task.
GALLERY_TTL_DAYS = {
    'FREE':       30,
    'PRO':        365,
    'ENTERPRISE': 0,
}

# GDPR: soft-delete on expiry, then hard-delete from R2 after this grace period.
GALLERY_HARD_DELETE_GRACE_DAYS = 30


# ============================================================
# 22. OOM PROTECTION
# ============================================================
# Nginx client_max_body_size is layer 1. This is the Django safety net.
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024   # 5 MB
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024   # 2 MB — above this, stream to tmp disk


# ============================================================
# 23. LOGGING
# ============================================================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} {process:d} {thread:d} - {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class':     'logging.StreamHandler',
            'formatter': 'verbose',
            'level':     'INFO',
        },
    },
    'loggers': {
        'django':    {'handlers': ['console'], 'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO'), 'propagate': False},
        'core':      {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'gallery':   {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'billing':   {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'ingestion': {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'webhooks':  {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
        'checkout':  {'handlers': ['console'], 'level': 'INFO', 'propagate': False},
    },
}


# ============================================================
# 24. SENTRY (production error monitoring)
# ============================================================
SENTRY_DSN = os.environ.get('SENTRY_DSN', '')
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from core.security import sentry_before_breadcrumb, sentry_before_send

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[
            DjangoIntegration(transaction_style='url', middleware_spans=True),
            CeleryIntegration(monitor_beat_tasks=True),
            LoggingIntegration(level=None, event_level='ERROR'),
        ],
        traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.2')),
        release=os.environ.get('APP_VERSION', 'photobox-api@dev'),
        environment=os.environ.get('SENTRY_ENVIRONMENT', 'development'),
        send_default_pii=False,   # GDPR: never send passwords / tokens to Sentry
        before_send=sentry_before_send,
        before_breadcrumb=sentry_before_breadcrumb,
    )


# ============================================================
# 25. CUSTOM TEST RUNNER
# ============================================================
TEST_RUNNER = 'core.utils.test_runner.EnterpriseTestRunner'


# ============================================================
# 26. TEST-MODE OVERRIDES
#     Everything below this line is ONLY applied when Django
#     is launched with the 'test' management command.
#     All external service stubs live here — nowhere else.
# ============================================================
if _IS_TEST:
    ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS + ["testserver", "localhost"]))

    # -- Celery: run tasks synchronously, no broker required --
    CELERY_TASK_ALWAYS_EAGER    = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_TASK_STORE_EAGER_RESULT = True

    # Keep test uploads entirely off the repository filesystem.
    # Django<4.1 has no built-in InMemoryStorage, so we wire in a tiny
    # process-local backend for FileField/ImageField saves during tests.
    DEFAULT_FILE_STORAGE = 'core.utils.test_storage.InMemoryTestStorage'

    # Accepted test uploads stay in RAM instead of spilling to temp files.
    FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

    # -- JWT: guarantee the signing key meets RFC 7518 minimum (32 bytes) --
    # This eliminates the InsecureKeyLengthWarning that pollutes test output.
    SIMPLE_JWT['SIGNING_KEY'] = os.environ.get(
        'JWT_SIGNING_KEY',
        'test-signing-key-that-is-at-least-32-bytes-long!!'
    )

    # -- External service stubs --
    # Real values are used if present in the environment (e.g. integration CI).
    # Safe dummies are the fallback so the unit-test suite needs zero .env setup.
    CLOUDFLARE_WEBHOOK_SECRET        = os.environ.get('CLOUDFLARE_WEBHOOK_SECRET',        'test-webhook-secret')
    CLOUDFLARE_R2_ENDPOINT           = os.environ.get('CLOUDFLARE_R2_ENDPOINT',           'https://test.r2.cloudflarestorage.com')
    CLOUDFLARE_R2_BUCKET_NAME        = os.environ.get('CLOUDFLARE_R2_BUCKET_NAME',        'test-bucket')
    CLOUDFLARE_R2_DOMAIN             = os.environ.get('CLOUDFLARE_R2_DOMAIN',             'test-r2-domain.example.com')
    CLOUDFLARE_ACCESS_KEY_ID         = os.environ.get('CLOUDFLARE_ACCESS_KEY_ID',         'test-key-id')
    CLOUDFLARE_SECRET_ACCESS_KEY     = os.environ.get('CLOUDFLARE_SECRET_ACCESS_KEY',     'test-secret-key')
    CLOUDINARY_CLOUD_NAME            = os.environ.get('CLOUDINARY_CLOUD_NAME',            'test-cloud')
    LEMON_SQUEEZY_API_KEY            = os.environ.get('LEMON_SQUEEZY_API_KEY',            'test-ls-api-key')
    LEMON_SQUEEZY_STORE_ID           = os.environ.get('LEMON_SQUEEZY_STORE_ID',           '1')
    LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY   = os.environ.get('LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY',   'test-ls-webhook-secret')
    LEMON_SQUEEZY_WEBHOOK_SECRET_SECONDARY = os.environ.get('LEMON_SQUEEZY_WEBHOOK_SECRET_SECONDARY', '')
    EMAIL_BACKEND = os.environ.get(
        'EMAIL_BACKEND',
        'django.core.mail.backends.locmem.EmailBackend',
    )

    # -- Throttling --
    # Global throttling is left ON with LocMemCache so throttle-verification
    # tests work correctly. Tests that must NOT be rate-limited patch their
    # view's throttle_classes directly (as they already do).
    # The cache is already set to LocMemCache above when _IS_TEST is True.

    # -- DRF: strip the schema class in tests to avoid spurious OpenAPI warnings --
    REST_FRAMEWORK['DEFAULT_SCHEMA_CLASS'] = 'rest_framework.schemas.openapi.AutoSchema'



