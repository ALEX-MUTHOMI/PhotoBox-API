---
title: Infrastructure & Configuration
description: Required environment variables and worker commands for local and production operation.
---

## Required environment variables

```bash
# Database
DB_HOST=localhost
DB_NAME=photobox
DB_USER=postgres
DB_PASS=your_password

# Django
SECRET_KEY=your-django-secret-key
DEBUG=0
ALLOWED_HOSTS=127.0.0.1,localhost

# Cloudflare R2
CLOUDFLARE_R2_BUCKET_NAME=photobox-vault
CLOUDFLARE_R2_ENDPOINT=https://ACCOUNT_ID.r2.cloudflarestorage.com
CLOUDFLARE_ACCESS_KEY_ID=your_r2_access_key
CLOUDFLARE_SECRET_ACCESS_KEY=your_r2_secret_key
CLOUDFLARE_R2_DOMAIN=pub-HASH.r2.dev
CLOUDFLARE_WEBHOOK_SECRET=your_webhook_signing_secret

# Cloudinary
CLOUDINARY_CLOUD_NAME=your_cloud_name

# Celery
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

# Sentry
SENTRY_DSN=https://examplePublicKey@o0.ingest.sentry.io/0
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_SAMPLE_RATE=0.2

# Email
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.sendgrid.net
EMAIL_PORT=587
EMAIL_HOST_USER=apikey
EMAIL_HOST_PASSWORD=SG.xxxxx
DEFAULT_FROM_EMAIL=PhotoBox <no-reply@photobox.app>

# CORS and frontend
CORS_ALLOWED_ORIGINS=https://app.photobox.app,https://www.photobox.app
FRONTEND_URL=https://app.photobox.app
```

## Worker commands

```bash
celery -A app worker --loglevel=info --concurrency=4
celery -A app beat --loglevel=info
celery -A app flower --port=5555
```

## Infrastructure notes

- Redis carries both queueing and throttle-cache responsibility, so availability matters beyond Celery itself.
- Cloudflare R2 is the durable binary store, which means backup and access policies should treat it as production data, not a cache.
- Cloudinary is a delivery optimization layer and should not be treated as authoritative storage.
