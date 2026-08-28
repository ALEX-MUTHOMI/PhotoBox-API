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


def _env_bool_strict(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default

    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ImproperlyConfigured(
        f"Invalid boolean value for {name}: must be one of true, 1, yes, on, "
        f"false, 0, no, off. Received {value!r}."
    )


DEBUG = _env_bool_strict("DEBUG", default=False)


def _detect_test_mode() -> bool:
    """
    Detect both Django's built-in test runner and pytest/pytest-django.

    The previous exact-token check only matched `manage.py test`, which meant
    `pytest gallery/tests/...` skipped the in-memory storage and eager Celery
    safeguards. That made the suite behave differently in Docker/CI vs local
    Django test runs.

    Explicit TESTING=0/false always wins so production boot checks can run
    from a pytest-spawned subprocess.
    """
    testing_raw = os.environ.get("TESTING")
    if testing_raw is not None:
        normalized = testing_raw.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True

    argv = [str(arg).lower() for arg in sys.argv]
    if any(
        arg in {"test", "pytest", "pytest.exe", "py.test"}
        or arg.endswith("\\pytest.exe")
        or arg.endswith("/pytest")
        or arg.endswith("/pytest.exe")
        or arg.endswith("\\pytest")
        for arg in argv
    ):
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
    if not (os.environ.get('DJANGO_SECRET_KEY') or os.environ.get('SECRET_KEY')):
        _missing.append('DJANGO_SECRET_KEY or SECRET_KEY')

    if not DEBUG:
        if not os.environ.get('ALLOWED_HOSTS'):
            _missing.append('ALLOWED_HOSTS')
        if not os.environ.get('CORS_ALLOWED_ORIGINS'):
            _missing.append('CORS_ALLOWED_ORIGINS')
        if not os.environ.get('CSRF_TRUSTED_ORIGINS'):
            _missing.append('CSRF_TRUSTED_ORIGINS')
        if not (
            os.environ.get('TURNSTILE_SECRET_KEY')
            or os.environ.get('CLOUDFLARE_TURNSTILE_SECRET_KEY')
        ):
            _missing.append('TURNSTILE_SECRET_KEY')
        if not os.environ.get('FRONTEND_URL'):
            _missing.append('FRONTEND_URL')

    if _missing:
        raise ImproperlyConfigured(
            f"CRITICAL: Required environment variables are missing: {_missing}. "
            f"Check your .env file."
        )


# ============================================================
# 2. CORE DJANGO SETTINGS
# ============================================================
SECRET_KEY = (
    os.environ.get('DJANGO_SECRET_KEY')
    or os.environ.get('SECRET_KEY')
    or 'INSECURE-LOCAL-DEV-KEY-DO-NOT-USE-IN-PROD'
)

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get(
        'ALLOWED_HOSTS',
        '127.0.0.1,localhost,.ngrok.app,.ngrok-free.app'
    ).split(',')
    if host.strip()
]

FRONTEND_URL = os.environ.get('FRONTEND_URL', 'http://localhost:3000')

IP_HASH_SALT = os.environ.get('IP_HASH_SALT', '')
LOG_SCRUBBER_SALT = os.environ.get('LOG_SCRUBBER_SALT', '')

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
    SECURE_PROXY_SSL_HEADER        = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_REDIRECT_EXEMPT = [
        r'^api/health-check/?$',
        r'^health/?$',
    ]

    if '*' in ALLOWED_HOSTS:
        raise ImproperlyConfigured("ALLOWED_HOSTS must not contain '*' when DEBUG=False.")


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
    'django.contrib.postgres',

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
    'allauth.account.middleware.AccountMiddleware',
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
# PgBouncer transaction pooling: hold no server-side cursors and recycle
# connections every request (CONN_MAX_AGE=0). Direct Postgres can raise
# DB_CONN_MAX_AGE if desired; never reintroduce .iterator() for archives.
DATABASES = {
    'default': {
        'ENGINE':   'django.db.backends.postgresql',
        'HOST':     os.environ.get('DB_HOST'),
        'NAME':     os.environ.get('DB_NAME'),
        'USER':     os.environ.get('DB_USER'),
        'PASSWORD': os.environ.get('DB_PASS'),
        'PORT':     os.environ.get('DB_PORT', '5432'),
        'CONN_MAX_AGE': int(os.environ.get('DB_CONN_MAX_AGE', '0')),
        'DISABLE_SERVER_SIDE_CURSORS': _env_flag(
            'DISABLE_SERVER_SIDE_CURSORS', default=True
        ),
    }
}

if _IS_TEST and not os.environ.get('DB_NAME'):
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'test_db.sqlite3',
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
        'magic_link_send': '3/minute',
        'magic_link_consume': '10/minute',
        'guest_access': '10/minute',
        'favorite_selection': '30/minute',
        'gallery_session_read': '120/minute',
        'password_reset_request': '3/minute',  # nosec B105 - throttle scope label, not a secret.
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

GALLERY_ACCESS_COOKIE_NAME = os.environ.get('GALLERY_ACCESS_COOKIE_NAME', 'gallery_access')
GALLERY_ACCESS_COOKIE_SAMESITE = os.environ.get('GALLERY_ACCESS_COOKIE_SAMESITE', 'Lax')
GALLERY_ACCESS_TOKEN_LIFETIME_SECONDS = int(
    os.environ.get('GALLERY_ACCESS_TOKEN_LIFETIME_SECONDS', 1800)
)


# ============================================================
# 14. ALLAUTH / DJ-REST-AUTH: IDENTITY FEDERATION
# ============================================================
REST_USE_JWT = True
JWT_AUTH_COOKIE         = 'access'
JWT_AUTH_REFRESH_COOKIE = 'refresh'

ACCOUNT_USER_MODEL_USERNAME_FIELD  = None
ACCOUNT_USERNAME_REQUIRED          = False
ACCOUNT_EMAIL_REQUIRED             = True
ACCOUNT_AUTHENTICATION_METHOD      = 'email'
ACCOUNT_LOGIN_METHODS              = {'email'}
ACCOUNT_SIGNUP_FIELDS              = ['email*', 'password1*', 'password2*']

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

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        'CSRF_TRUSTED_ORIGINS',
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
CLOUDFLARE_WORKER_SHARED_SECRET = os.environ.get('CLOUDFLARE_WORKER_SHARED_SECRET', '')
CLOUDFLARE_ACCESS_KEY_ID        = os.environ.get('CLOUDFLARE_ACCESS_KEY_ID', '')
CLOUDFLARE_SECRET_ACCESS_KEY    = os.environ.get('CLOUDFLARE_SECRET_ACCESS_KEY', '')
CLOUDFLARE_WEBHOOK_SECRET       = os.environ.get('CLOUDFLARE_WEBHOOK_SECRET', '')
TURNSTILE_SECRET_KEY = (
    os.environ.get('TURNSTILE_SECRET_KEY')
    or os.environ.get('CLOUDFLARE_TURNSTILE_SECRET_KEY', '')
)
CLOUDFLARE_TURNSTILE_SECRET_KEY = TURNSTILE_SECRET_KEY
TURNSTILE_FAIL_OPEN = _env_flag('TURNSTILE_FAIL_OPEN', default=False)
CLOUDFLARE_R2_DELETE_ENDPOINT   = os.environ.get('CLOUDFLARE_R2_DELETE_ENDPOINT', '')
CLOUDFLARE_R2_DELETE_BUCKET_NAME = os.environ.get('CLOUDFLARE_R2_DELETE_BUCKET_NAME', '')
CLOUDFLARE_R2_DELETE_ACCESS_KEY_ID = os.environ.get('CLOUDFLARE_R2_DELETE_ACCESS_KEY_ID', '')
CLOUDFLARE_R2_DELETE_SECRET_ACCESS_KEY = os.environ.get('CLOUDFLARE_R2_DELETE_SECRET_ACCESS_KEY', '')


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
CELERY_WORKER_MAX_TASKS_PER_CHILD = int(
    os.environ.get('CELERY_WORKER_MAX_TASKS_PER_CHILD', 50)
)

# Beat schedule registry — Plans 02/04 register into their slots.
from celery.schedules import crontab  # noqa: E402

GALLERY_LIFECYCLE_BEAT_SCHEDULE = {
    'gallery-expire-due-galleries': {
        'task': 'gallery.retention.expire_due_galleries',
        'schedule': crontab(hour=3, minute=0),
        'options': {'queue': 'default', 'expires': 60 * 60 * 20},
    },
    'gallery-hard-delete-expired': {
        'task': 'gallery.retention.hard_delete_expired_galleries',
        'schedule': crontab(hour=4, minute=30),
        'options': {'queue': 'default', 'expires': 60 * 60 * 20},
    },
}
RETENTION_BEAT_SCHEDULE = {
    'retention-purge-expired-archives': {
        'task': 'gallery.retention.purge_expired_archives',
        'schedule': crontab(minute=15),
        'options': {'queue': 'default', 'expires': 60 * 50},
    },
    'retention-purge-expired-magic-links': {
        'task': 'gallery.retention.purge_expired_magic_links',
        'schedule': crontab(hour=2, minute=0),
        'options': {'queue': 'default', 'expires': 60 * 60 * 20},
    },
    'retention-prune-billing-ledgers': {
        'task': 'billing.retention.prune_billing_ledgers',
        'schedule': crontab(hour=5, minute=0, day_of_week=0),
        'options': {'queue': 'default', 'expires': 60 * 60 * 20},
    },
}
HEAVY_LANE_LOCK_TIMEOUT_MS = int(os.environ.get('HEAVY_LANE_LOCK_TIMEOUT_MS', 3000))
INGESTION_BEAT_SCHEDULE = {
    'reap-abandoned-uploads': {
        'task': 'ingestion.tasks.reap_abandoned_uploads',
        'schedule': crontab(hour='*/6'),
        'options': {'queue': 'default', 'expires': 60 * 60 * 5},
    },
}
CLIENT_ACCESS_BEAT_SCHEDULE = {
    'purge-expired-gallery-access-artifacts': {
        'task': 'gallery.tasks.purge_expired_gallery_access_artifacts',
        'schedule': crontab(hour=3, minute=0),
        'options': {'queue': 'default', 'expires': 60 * 60 * 20},
    },
}

CELERY_BEAT_SCHEDULE = {
    **GALLERY_LIFECYCLE_BEAT_SCHEDULE,
    **RETENTION_BEAT_SCHEDULE,
    **INGESTION_BEAT_SCHEDULE,
    **CLIENT_ACCESS_BEAT_SCHEDULE,
}

PHOTO_MAX_IMAGE_PIXELS = int(os.environ.get('PHOTO_MAX_IMAGE_PIXELS', 89_478_485))


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
GALLERY_ARCHIVE_TTL_HOURS = int(os.environ.get('GALLERY_ARCHIVE_TTL_HOURS', 24))
ARCHIVE_ZIP_GLOBAL_LEASES = int(os.environ.get('ARCHIVE_ZIP_GLOBAL_LEASES', 20))
ARCHIVE_ZIP_PER_GALLERY_LEASES = int(os.environ.get('ARCHIVE_ZIP_PER_GALLERY_LEASES', 1))
ARCHIVE_ZIP_LEASE_TTL_SECONDS = int(os.environ.get('ARCHIVE_ZIP_LEASE_TTL_SECONDS', 60))
ARCHIVE_ZIP_LEASE_HEARTBEAT_SECONDS = int(
    os.environ.get('ARCHIVE_ZIP_LEASE_HEARTBEAT_SECONDS', 10)
)
ARCHIVE_ZIP_QUEUE = os.environ.get('ARCHIVE_ZIP_QUEUE', 'archive-zip')
GALLERY_PIN_MAX_FAILED_ATTEMPTS = int(os.environ.get('GALLERY_PIN_MAX_FAILED_ATTEMPTS', 10))
GALLERY_PIN_LOCKOUT_SECONDS = int(os.environ.get('GALLERY_PIN_LOCKOUT_SECONDS', 900))
GALLERY_MAGIC_LINK_MAX_LIVE = int(os.environ.get('GALLERY_MAGIC_LINK_MAX_LIVE', 5))
GALLERY_SESSION_RETENTION_DAYS = int(os.environ.get('GALLERY_SESSION_RETENTION_DAYS', 30))
GALLERY_PUBLIC_PHOTOS_PER_SCENE = int(
    os.environ.get('GALLERY_PUBLIC_PHOTOS_PER_SCENE', 100)
)

# Web derivative / watermark (Celery generate_photo_web_derivative)
PHOTO_WEB_MAX_DIMENSION = int(os.environ.get('PHOTO_WEB_MAX_DIMENSION', 2400))
PHOTO_WEB_QUALITY = int(os.environ.get('PHOTO_WEB_QUALITY', 86))
PHOTO_WATERMARK_SCALE_RATIO = float(os.environ.get('PHOTO_WATERMARK_SCALE_RATIO', 0.22))
# Optional SAT corner selection (Phase 3) — default off keeps bottom-right
PHOTO_WATERMARK_SAT_CORNER_SELECTION = _env_flag(
    'PHOTO_WATERMARK_SAT_CORNER_SELECTION', default=False
)
PHOTO_WATERMARK_SAT_MIN_PIXELS = int(
    os.environ.get('PHOTO_WATERMARK_SAT_MIN_PIXELS', 160_000)
)
PHOTO_WATERMARK_SAT_MAX_SIDE = int(os.environ.get('PHOTO_WATERMARK_SAT_MAX_SIDE', 800))
PHOTO_WATERMARK_SAT_TIE_EPSILON = float(
    os.environ.get('PHOTO_WATERMARK_SAT_TIE_EPSILON', 1.0)
)

# Phase 4 burst clustering (offline Celery)
PHOTO_PHASH_VERSION = int(os.environ.get('PHOTO_PHASH_VERSION', 1))
PHOTO_PHASH_HAMMING_THRESHOLD = int(os.environ.get('PHOTO_PHASH_HAMMING_THRESHOLD', 8))
PHOTO_BURST_TIME_WINDOW_SECONDS = int(
    os.environ.get('PHOTO_BURST_TIME_WINDOW_SECONDS', 90)
)
PHOTO_PHASH_LSH_BANDS = int(os.environ.get('PHOTO_PHASH_LSH_BANDS', 8))
PHOTO_PHASH_LSH_ROWS = int(os.environ.get('PHOTO_PHASH_LSH_ROWS', 8))
PHOTO_CLUSTER_DEBOUNCE_SECONDS = int(
    os.environ.get('PHOTO_CLUSTER_DEBOUNCE_SECONDS', 30)
)
TRUST_CLOUDFLARE_CLIENT_IP = os.environ.get('TRUST_CLOUDFLARE_CLIENT_IP', 'false').lower() in (
    '1', 'true', 'yes',
)
CURRENT_TOS_VERSION = os.environ.get('CURRENT_TOS_VERSION', '2026-04')

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
TEST_RUNNER = 'core.utils.enterprise_runner.EnterpriseTestRunner'


# ============================================================
# 26. TEST-MODE OVERRIDES
#     Everything below this line is ONLY applied when Django
#     is launched with the 'test' management command.
#     All external service stubs live here — nowhere else.
# ============================================================
if _IS_TEST:
    ALLOWED_HOSTS = list(dict.fromkeys(ALLOWED_HOSTS + ["testserver", "localhost"]))
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

    # -- Celery: run tasks synchronously, no broker required --
    CELERY_BROKER_URL = 'memory://'
    CELERY_RESULT_BACKEND = 'cache+memory://'
    CELERY_TASK_ALWAYS_EAGER    = True
    CELERY_TASK_EAGER_PROPAGATES = True
    CELERY_TASK_STORE_EAGER_RESULT = False

    # Keep test uploads entirely off the repository filesystem.
    # Django<4.1 has no built-in InMemoryStorage, so we wire in a tiny
    # process-local backend for FileField/ImageField saves during tests.
    DEFAULT_FILE_STORAGE = 'core.utils.inmemory_storage.InMemoryTestStorage'

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
    CLOUDFLARE_WORKER_SHARED_SECRET  = os.environ.get(
        'CLOUDFLARE_WORKER_SHARED_SECRET', 'test-worker-shared-secret'
    )
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



