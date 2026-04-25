---
title: Deployment
description: Production checklist and observability notes for the PhotoBox platform.
---

## Production checklist

- Set `DEBUG=0`.
- Use a long, random `SECRET_KEY`.
- Configure `ALLOWED_HOSTS` for the production domain.
- Restrict `CORS_ALLOWED_ORIGINS` to trusted frontend hosts.
- Scope R2 IAM permissions to the minimum required object actions.
- Use a dedicated `CLOUDFLARE_WEBHOOK_SECRET`.
- Configure production email credentials.
- Set `SENTRY_DSN` and `SENTRY_ENVIRONMENT=production`.
- Run `python manage.py migrate`.
- Run `python manage.py collectstatic`.
- Start Celery worker and Celery Beat.
- Enforce `client_max_body_size 5m` in Nginx.
- Enable HTTPS.
- Run the test suite before promotion.

## Sentry integration

Sentry is enabled when `SENTRY_DSN` is present. The current posture is designed to be operationally useful without leaking obvious sensitive data:

- Django exceptions are captured automatically.
- Celery task failures are captured from worker execution.
- Performance traces are sampled from `SENTRY_TRACES_SAMPLE_RATE`.
- Breadcrumb scrubbers remove tokens, cookies, emails, and inline bearer secrets before export.

## Docs portal deployment

The Starlight site itself is a static build. A safe production path looks like this:

1. Build with `npm run check` and `npm run build`.
2. Publish the generated `dist/` directory to a static host or CDN-backed bucket.
3. Put the site behind HTTPS and a CDN cache.
4. Set `DOCS_SITE_URL` so canonical links and sitemap output are correct.
