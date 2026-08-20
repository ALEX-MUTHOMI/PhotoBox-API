#!/usr/bin/env bash
set -eo pipefail

cat > .env << 'ENVEOF'
SECRET_KEY=ci-only-insecure-secret-key-do-not-use-in-prod-aabbccdd
DEBUG=0
ALLOWED_HOSTS=localhost,127.0.0.1,app,0.0.0.0
CSRF_TRUSTED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
DB_HOST=db
DB_NAME=photobox_ci
DB_USER=photobox_ci_user
DB_PASS=ci_db_pass_not_used_externally
POSTGRES_DB=photobox_ci
POSTGRES_USER=photobox_ci_user
POSTGRES_PASSWORD=ci_db_pass_not_used_externally
REDIS_URL=redis://redis:6379/1
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
CLOUDFLARE_R2_ENDPOINT=https://stub.r2.cloudflarestorage.com
CLOUDFLARE_R2_BUCKET_NAME=ci-test-bucket
CLOUDFLARE_R2_DOMAIN=ci-test.r2.dev
CLOUDFLARE_ACCESS_KEY_ID=ci-access-key-id
CLOUDFLARE_SECRET_ACCESS_KEY=ci-secret-access-key
CLOUDFLARE_WEBHOOK_SECRET=ci-webhook-secret
CLOUDINARY_CLOUD_NAME=ci-cloud
LEMON_SQUEEZY_API_KEY=ci-ls-api-key
LEMON_SQUEEZY_STORE_ID=0
LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY=ci-ls-primary-secret
LEMON_SQUEEZY_WEBHOOK_SECRET_SECONDARY=ci-ls-secondary-secret
FRONTEND_URL=http://localhost:3000
EMAIL_BACKEND=django.core.mail.backends.locmem.EmailBackend
DJANGO_LOG_LEVEL=WARNING
TURNSTILE_SECRET_KEY=ci-turnstile-stub
IP_HASH_SALT=ci-ip-salt-stub
REDIS_PASSWORD=ci-redis-password
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,app,0.0.0.0
SENTRY_DSN=
ENVEOF

echo "CI env generated with redacted values."
