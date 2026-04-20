---
title: System Components
description: The core Django apps and external services that make up the PhotoBox platform.
---

## Django applications

| App | Purpose | Key areas |
| --- | --- | --- |
| `core` | User model, workspace tenancy, and legacy gallery ownership | `models.py` |
| `gallery` | Event, scene, and photo CRUD plus Fast Lane uploads | `views.py`, `tasks.py`, `storage.py` |
| `ingestion` | Heavy Lane manifests and presigned upload tickets | `views.py`, `serializers.py` |
| `webhooks` | Cloudflare R2 completion signals and legacy ingress | `views.py` |
| `billing` | Subscription lifecycle and provider reconciliation | `urls.py`, webhook flows |
| `checkout` | Payment entry points and checkout security | `urls.py` |
| `user` | Auth, registration, token flows, and social hardening | `serializers.py`, adapters |

## External services

| Service | Role | Primary configuration |
| --- | --- | --- |
| Cloudflare R2 | Durable binary vault for all uploaded media | `CLOUDFLARE_ACCESS_KEY_ID`, `CLOUDFLARE_SECRET_ACCESS_KEY` |
| Cloudinary | Fetch-based CDN delivery and WebP optimization | `CLOUDINARY_CLOUD_NAME` |
| Redis | Celery broker, result backend, and API throttling cache | `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` |
| PostgreSQL | Primary transactional database | `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASS` |
| Sentry | Production error and performance monitoring | `SENTRY_DSN` |
| SendGrid or SES | Transactional email delivery | `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` |

## Service boundaries

### Request path

The synchronous HTTP request path validates ownership, enforces quotas, and commits the minimal database state needed to represent work in progress.

### Worker path

Celery handles the slow or failure-prone work:

- Fast Lane vault uploads
- Gallery-ready email dispatch
- Nightly retention jobs
- Billing reconciliation and other asynchronous loops

### Delivery path

The media delivery plane stays separate from ingestion:

- Cloudinary handles gallery viewing and image optimization.
- R2 presigned GET URLs handle downloads with short-lived access windows.
