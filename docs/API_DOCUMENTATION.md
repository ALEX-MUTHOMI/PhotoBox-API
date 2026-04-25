# PhotoBox API — Enterprise Architecture Documentation

> **Version:** 2.0 (Unified Vault / EDA)  
> **Last Updated:** April 2026  
> **Maintainer:**  ALEX MPUTHIA

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [System Components](#system-components)
3. [Data Model (Event → Scene → Photo)](#data-model)
4. [Upload Pipelines](#upload-pipelines)
5. [Delivery Layer](#delivery-layer)
6. [Security Architecture](#security-architecture)
7. [Webhook System](#webhook-system)
8. [Notification System](#notification-system)
9. [API Reference](#api-reference)
10. [Infrastructure & Configuration](#infrastructure--configuration)
11. [Testing](#testing)
12. [Deployment](#deployment)

---

## Architecture Overview

PhotoBox is a multi-tenant photography SaaS platform built on **Django 4.x** with an
**Event-Driven Architecture (EDA)**. The system serves professional photographers who
upload, curate, and deliver galleries to their clients.

### The Unified Vault Pattern

```
┌─────────────────────────────────────────────────────────┐
│                   PHOTOGRAPHER                          │
│              (React Dashboard)                          │
└──────────┬──────────────────┬───────────────────────────┘
           │                  │
     ≤ 5 MB files        > 5 MB files
     (JPEG/PNG/WebP)     (RAW/Video/Bulk)
           │                  │
           ▼                  ▼
   ┌──────────────┐   ┌─────────────────┐
   │  FAST LANE   │   │   HEAVY LANE    │
   │  /api/gallery│   │  /api/v1/       │
   │  /fast-lane/ │   │  ingestion/     │
   │  photos/     │   │  bulk/          │
   └──────┬───────┘   └────────┬────────┘
          │                    │
          │ 202 Accepted       │ Presigned POST
          │ + Celery task      │ tickets → R2
          │                    │
          ▼                    ▼
   ┌─────────────────────────────────────┐
   │       CLOUDFLARE R2 VAULT           │
   │    (Single Source of Truth)          │
   │    Tenant-isolated object keys      │
   └──────────────┬──────────────────────┘
                  │
          ┌───────┴───────┐
          │               │
          ▼               ▼
   ┌─────────────┐ ┌──────────────┐
   │ Cloudinary  │ │ R2 Presigned │
   │ Fetch Proxy │ │ GET URL      │
   │ (WebP CDN)  │ │ (Download)   │
   │ delivery_url│ │ download_url │
   └─────────────┘ └──────────────┘
          │               │
          ▼               ▼
   ┌─────────────────────────────────────┐
   │           CLIENT BROWSER            │
   │        (Gallery Viewer)             │
   └─────────────────────────────────────┘
```

### Key Principles

| Principle | Implementation |
|---|---|
| **Single Source of Truth** | Cloudflare R2 stores ALL binaries. Cloudinary is a CDN proxy only. |
| **Async-First** | Heavy Lane signing and DB commits stay in-request; all slow storage reconciliation and Fast Lane vault uploads run out-of-band via Celery. |
| **Tenant Isolation** | Every DB query enforces `workspace__user=request.user` join chain. |
| **Defense in Depth** | Nginx → Django OOM limits → Pillow magic bytes → Quota gate → Celery. |

---

## System Components

### Django Apps

| App | Purpose | Key Files |
|---|---|---|
| `core` | User model, Workspace, Gallery (legacy) | `models.py` |
| `gallery` | Event/Scene/Photo CRUD, Fast Lane uploads | `views.py`, `tasks.py`, `storage.py` |
| `ingestion` | Heavy Lane bulk manifest + presigned tickets | `views.py`, `serializers.py` |
| `webhooks` | Cloudflare R2 upload completion signals | `views.py` |
| `billing` | Stripe/Lemon Squeezy subscription management | `urls.py` |
| `checkout` | Payment flow | `urls.py` |
| `user` | Authentication, registration | `serializers.py` |

### External Services

| Service | Role | Credentials |
|---|---|---|
| **Cloudflare R2** | Binary storage vault | `CLOUDFLARE_ACCESS_KEY_ID`, `CLOUDFLARE_SECRET_ACCESS_KEY` |
| **Cloudinary** | CDN Fetch Proxy (WebP transform + edge cache) | `CLOUDINARY_CLOUD_NAME` |
| **Redis** | Celery broker + result backend + DRF throttle cache | `CELERY_BROKER_URL` |
| **PostgreSQL** | Primary database | `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASS` |
| **Sentry** | Production error monitoring + performance tracing | `SENTRY_DSN` |
| **SendGrid/SES** | Transactional email delivery | `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` |

---

## Data Model

### Entity Hierarchy

```
Workspace (Tenant Boundary)
  └── Event (The Gig — e.g., "The Kamau Wedding")
        ├── client_email, client_name (notification targets)
        ├── is_published (manual gallery release trigger)
        ├── _hashed_pin (Argon2 gallery access control)
        └── Scene (Tab — e.g., "Ceremony", "Reception")
              └── Photo / MediaAsset (The Asset)
                    ├── r2_object_key (vault path)
                    ├── status: PENDING → READY | FAILED | QUARANTINED | EXPIRED
                    ├── is_processed: True when R2 upload confirmed
                    ├── width, height (masonry grid dimensions)
                    ├── delivery_url (Cloudinary Fetch proxy)
                    └── download_url (R2 presigned GET, 60-second TTL)
```

### Photo Status State Machine

```
PENDING ──┬──→ READY          (Celery task success / Webhook PutObject)
          ├──→ FAILED         (All Celery retries exhausted → quota refunded)
          ├──→ QUARANTINED    (Webhook size mismatch → held for review)
          └──→ EXPIRED        (Gallery TTL exceeded → nightly purge task)
```

### MediaAsset Alias

`MediaAsset = Photo` — The ingestion app imports `MediaAsset` from `gallery.models`.
This is an alias to `Photo`, so both names reference the same database table.
This avoids a migration while keeping the ingestion code semantically clean.

---

## Upload Pipelines

### Fast Lane (≤ 5 MB, synchronous accept)

**Endpoint:** `POST /api/gallery/fast-lane/photos/`  
**Auth:** JWT Bearer token  
**Content-Type:** `multipart/form-data`  
**Throttle:** 30 uploads/minute per user

**Request Flow:**

1. **Payload size gate** — Rejects files > 5 MB before any processing.
2. **Cross-tenant shield** — Verifies the `scene` belongs to the authenticated user's workspace.
3. **Magic byte inspector** — Two-pass Pillow verification:
   - Pass 1: `probe.verify()` — structural integrity (destroys object)
   - Pass 2: `PILImage.open()` — dimensions check (100MP decompression bomb limit) + format allowlist (JPEG/PNG/WebP)
4. **Quota gate** — Checks `workspace.storage_used_bytes + file_size ≤ storage_limit_bytes`
5. **Atomic DB write** — Creates `Photo` with `is_processed=False`, `status='PENDING'`
6. **Celery dispatch** — `process_fast_lane_asset.delay(photo_id)` — returns **202 Accepted** immediately.

**Celery Task (`process_fast_lane_asset`):**

1. Reads local file from Django storage.
2. Streams to R2 via `boto3.upload_fileobj()`.
3. Sets `r2_object_key`, flips `is_processed=True`, `status='READY'`.
4. On permanent failure: marks `FAILED`, atomically refunds quota bytes.

### Heavy Lane (> 5 MB, presigned direct-to-R2)

**Endpoint:** `POST /api/v1/ingestion/bulk/`  
**Auth:** JWT Bearer token  
**Content-Type:** `application/json`  
**Throttle:** 10 manifests/minute per user

**Request Payload:**
```json
{
  "scene_id": "uuid",
  "files": [
    {
      "filename": "wedding_001.cr2",
      "file_size": 25000000,
      "client_reference_id": "ref-001"
    }
  ]
}
```

**Response (201 Created):**
```json
{
  "upload_tickets": [
    {
      "client_reference_id": "ref-001",
      "post_url": "https://your-bucket.r2.cloudflarestorage.com",
      "post_fields": { "key": "raw/tenant_.../...", "Policy": "...", "X-Amz-Signature": "..." }
    }
  ]
}
```

**Flow:**
1. Serializer validates filenames (null byte shield, XSS stripping, extension routing).
2. `SELECT FOR UPDATE` locks workspace quota atomically (prevents concurrent overspend).
3. Generates R2 presigned POST tickets with `content-length-range` constraints.
4. `bulk_create()` inserts all `MediaAsset` rows in one INSERT.
5. React client uploads directly to R2 using the tickets — Django is NOT in the data path.

### Event-Driven Control Loops

PhotoBox is event-driven in three different places, and they do not all have the same failure semantics:

1. **Fast Lane acceptance loop**
   `PhotoFastLaneViewSet.perform_create()` atomically reserves quota, creates a `Photo(status='PENDING')`, then dispatches `process_fast_lane_asset`.
   The worker performs the actual R2 upload, and `gallery.tasks` later self-heals or refunds abandoned uploads.

2. **Heavy Lane completion loop**
   `BulkIngestionView` signs direct-to-R2 upload tickets and inserts all `MediaAsset` rows in one transaction.
   `R2WebhookView` is the authoritative completion signal, with idempotent `PENDING -> READY | QUARANTINED` transitions.

3. **Billing reconciliation loop**
   `WebhookReceiverView` performs HMAC verification, derives a payload hash, and hands the event to Celery.
   `process_lemon_squeezy_webhook()` uses the payload hash as the idempotency key, not the unauthenticated `X-Event-ID` header, and preserves the Lemon Squeezy subscription id across cancellations so delayed `subscription_updated` events can reconcile state.

---

## Delivery Layer

### delivery_url (Cloudinary Fetch Proxy)

**Pattern:**
```
https://res.cloudinary.com/{CLOUDINARY_CLOUD_NAME}/image/fetch/q_auto,f_webp/{R2_PUBLIC_URL}
```

- Cloudinary fetches from R2 on first request.
- Automatically transcodes to WebP with quality optimization.
- Caches at 200+ edge locations globally.
- **No SDK upload ever occurs** — Cloudinary never stores the original.

### download_url (R2 Presigned GET)

- Generated on-demand via `generate_r2_presigned_get_url()`.
- **Hard TTL cap: 60 seconds** — enforced server-side in `storage.py`.
- Caller CANNOT bypass the cap by passing a larger `expires_in`.
- Both images and videos use this path. Large downloads go directly to R2 edge.

### aspect_ratio (Zero-Layout-Shift)

- `width / height` rounded to 4 decimal places.
- Enables React frontend to pre-allocate masonry card dimensions before image loads.
- Eliminates CLS (Cumulative Layout Shift) — a Core Web Vital metric.

---

## Security Architecture

### Layer 1: Transport & Perimeter

| Defense | Implementation |
|---|---|
| **Nginx OOM Gate** | `client_max_body_size 5m` — TCP-level drop before Django |
| **Django OOM Gate** | `DATA_UPLOAD_MAX_MEMORY_SIZE = 5MB` — second barrier |
| **CORS Strict Allowlist** | `CORS_ALLOW_ALL_ORIGINS = False` — explicit origin list |
| **Rate Limiting** | Fast Lane: 30/min, Heavy Lane: 10/min, Anon: 5/min |
| **JWT Authentication** | 60-min access tokens, 7-day rotating refresh tokens |
| **Password Hashing** | Argon2 primary, PBKDF2 fallback |

### Layer 2: Application Logic

| Defense | Implementation |
|---|---|
| **Tenant Isolation** | 4-join chain: `scene__event__workspace__user` on every query |
| **Cross-Tenant Shield** | `perform_create()` verifies scene ownership before writes |
| **Magic Byte Inspector** | Two-pass Pillow verify + format allowlist (JPEG/PNG/WebP) |
| **Decompression Bomb** | 100MP pixel ceiling via Pillow dimension check |
| **Quota Gate** | Atomic `F()` expression updates prevent race conditions |
| **Quota Refund** | `perform_destroy()` atomically refunds bytes on delete |

### Layer 3: Webhook Security (Machine-to-Machine)

| Defense | Implementation |
|---|---|
| **HMAC-SHA256** | `hmac.compare_digest()` — timing-safe comparison |
| **Replay Attack Window** | `Webhook-Timestamp` is mandatory for R2 and enforced with a 5-minute freshness window; Lemon Squeezy uses payload-hash idempotency because the provider event header is not authenticated |
| **Ghost Key Tolerance** | Unknown R2 keys → 200 (halts Cloudflare retry storms) |
| **Size Mismatch Quarantine** | Actual > declared → `QUARANTINED` status |
| **OOM Payload Guard** | Content-Length > 1MB → rejected before parsing |
| **Secret Separation** | `CLOUDFLARE_WEBHOOK_SECRET` ≠ `CLOUDFLARE_SECRET_ACCESS_KEY` |

### Billing Integrity Notes

- `GenerateCheckoutLinkView` blocks duplicate upgrades by reading `request.user.subscription.is_pro`, not a transient attribute on the `User` model.
- Lemon Squeezy idempotency is keyed from the raw payload hash. This closes the replay path where an intercepted valid payload is resent with a forged `X-Event-ID`.
- Cancellation keeps `lemon_squeezy_subscription_id` for reconciliation and forensics. Downgrading no longer severs the only foreign key that a delayed renewal needs to find the tenant again.

### Identity Hardening Notes

- Google social sign-in is governed by `user.adapters.HardenedSocialAccountAdapter`.
- Social identities without a verified email claim are rejected fail-closed.
- If a local account already exists for the email and the incoming Google identity is not already linked to that exact provider `uid`, the login is rejected with a conflict instead of auto-linking.
- Successful and failed password logins now log hashed principals and hashed IP fingerprints instead of raw email addresses and source IPs.

### Layer 4: Data Retention (GDPR)

| Tier | TTL | Enforcement |
|---|---|---|
| FREE | 30 days | Celery Beat nightly purge |
| PRO | 365 days | Celery Beat nightly purge |
| ENTERPRISE | Unlimited | Manual purge only |
| Grace Period | +30 days after soft-delete | Hard-delete from R2 |

---

## Webhook System

### Ingestion Webhook (New — Phase 2)

**Endpoint:** `POST /api/v1/ingestion/webhook/`  
**Auth:** HMAC-SHA256 (not JWT)  
**URL Name:** `r2-ingestion-webhook`

11-step security pipeline:
1. Content-Length guard (1MB max)
2. Raw body capture (before DRF parses stream)
3. Signature header extraction
4. HMAC-SHA256 verification (`hmac.compare_digest`)
5. Replay attack window (5 minutes)
6. JSON parse with explicit error handling
7. Action filter (only PutObject)
8. Ghost key tolerance (200 for unknown keys)
9. Size mismatch quarantine
10. Idempotency check (skip if already READY)
11. Atomic state transition (PENDING → READY)

### Legacy Webhook

**Endpoint:** `POST /api/v1/webhooks/cloudflare/r2/`  
**URL Name:** `r2-webhook-ingress`

Same security model. Transitions assets to `UPLOADED` status.

---

## Notification System

### Gallery Ready Email

**Trigger:** Manual only — when photographer toggles `is_published = True` on an Event.  
**Celery Task:** `gallery.notifications.send_gallery_ready_email`

**Flow:**
1. `EventViewSet.perform_update()` detects `is_published: False → True` transition.
2. Fires Celery task with `event_id`.
3. Task renders branded HTML email template (`email/gallery_ready.html`).
4. Sends via configured email backend (SendGrid/SES in production, console in dev).
5. Retries up to 3 times on failure (60s delay between retries).

**Security:**
- Only fires if `event.client_email` is set.
- Cross-tenant check enforced by `get_queryset()` — can't publish someone else's event.
- Race condition guard: task verifies `is_published` is still True at execution time.

---

## API Reference

### Gallery Endpoints

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/gallery/events/` | List photographer's events | JWT |
| `POST` | `/api/gallery/events/` | Create new event | JWT |
| `PATCH` | `/api/gallery/events/{id}/` | Update event (publish trigger) | JWT |
| `DELETE` | `/api/gallery/events/{id}/` | Delete event | JWT |
| `GET` | `/api/gallery/scenes/` | List scenes (filter by `?event=`) | JWT |
| `POST` | `/api/gallery/scenes/` | Create scene | JWT |
| `POST` | `/api/gallery/fast-lane/photos/` | Upload photo (≤5MB) → **202** | JWT |
| `GET` | `/api/gallery/fast-lane/photos/` | List photos (filter by `?scene=`) | JWT |
| `DELETE` | `/api/gallery/fast-lane/photos/{id}/` | Delete photo (refunds quota) | JWT |

### Ingestion Endpoints

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `POST` | `/api/v1/ingestion/bulk/` | Submit bulk upload manifest | JWT |
| `POST` | `/api/v1/ingestion/webhook/` | R2 upload completion signal | HMAC |

### System Endpoints

| Method | Endpoint | Description | Auth |
|---|---|---|---|
| `GET` | `/api/health-check/` | Server health probe | None |
| `GET` | `/api/schema/` | OpenAPI 3.0 schema | None |
| `GET` | `/api/docs/` | ReDoc API documentation | None |
| `POST` | `/api/user/create/` | Register new user | None |
| `POST` | `/api/user/token/` | Obtain JWT pair | None |

---

## Infrastructure & Configuration

### Required Environment Variables

```bash
# Database
DB_HOST=localhost
DB_NAME=photobox
DB_USER=postgres
DB_PASS=your_password

# Security
SECRET_KEY=your-django-secret-key
DEBUG=1
ALLOWED_HOSTS=127.0.0.1,localhost

# Cloudflare R2 (Unified Vault)
CLOUDFLARE_R2_BUCKET_NAME=photobox-vault
CLOUDFLARE_R2_ENDPOINT=https://ACCOUNT_ID.r2.cloudflarestorage.com
CLOUDFLARE_ACCESS_KEY_ID=your_r2_access_key
CLOUDFLARE_SECRET_ACCESS_KEY=your_r2_secret_key
CLOUDFLARE_R2_DOMAIN=pub-HASH.r2.dev
CLOUDFLARE_WEBHOOK_SECRET=your_webhook_signing_secret

# Cloudinary (CDN Proxy)
CLOUDINARY_CLOUD_NAME=your_cloud_name

# Celery
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0

# Sentry (Production Only)
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

# CORS
CORS_ALLOWED_ORIGINS=https://app.photobox.app,https://www.photobox.app

# Frontend
FRONTEND_URL=https://app.photobox.app
```

### Celery Worker Commands

```bash
# Start the async worker (processes Fast Lane uploads + emails)
celery -A app worker --loglevel=info --concurrency=4

# Start the scheduler (nightly gallery purge)
celery -A app beat --loglevel=info

# Monitor tasks in real-time
celery -A app flower --port=5555
```

---

## Testing

### Run All Tests

```bash
cd app/
python manage.py test gallery ingestion webhooks --verbosity=2
```

### Test Architecture

| Suite | File | Covers |
|---|---|---|
| Fast Lane API | `gallery/tests/test_fastlane_api.py` | Upload flow, 202 response, quota, magic bytes, decompression bomb, tenant isolation |
| R2 Webhook | `ingestion/tests/test_r2_webhook.py` | HMAC verification, replay attack, ghost keys, size mismatch quarantine, idempotency |
| Legacy Webhook | `webhooks/tests/test_cloudflare.py` | Signature validation, payload tampering, action filtering |
| Ingestion | `ingestion/tests/test_views.py` | Bulk manifest validation, presigned ticket generation, quota locking |

### Database Seeder

```bash
# Seed with demo data (only works when DEBUG=True)
python manage.py seed_db

# Flush and reseed
python manage.py seed_db --flush

# Generate larger relational datasets safely
python manage.py seed_db --flush --workspace-count 5 --events-per-workspace 10 --scenes-per-event 10 --photos-per-scene 25 --batch-size 500
```

Creates:
- 3 photographer accounts (alex@, jane@, admin@)
- 3 events with 2-5 scenes each
- ~100 photos with mocked R2 keys, delivery dimensions, and READY status
- Realistic filenames, file sizes, and aspect ratios

Scale notes:
- Photo rows are inserted with `bulk_create()` in bounded batches.
- Workspace quota is recomputed with a single aggregate query, not an in-memory full-table materialization.
- The command exposes scale controls for workspaces, events, scenes, photos, and batch size so you can reach 10k+ rows without row-by-row write amplification.

---

## Deployment

### Production Checklist

- [ ] Set `DEBUG=0` in `.env`
- [ ] Set real `SECRET_KEY` (≥50 chars, cryptographically random)
- [ ] Configure `ALLOWED_HOSTS` with actual domain
- [ ] Set `CORS_ALLOWED_ORIGINS` to production frontend URL only
- [ ] Configure R2 IAM credentials (scoped to `s3:PutObject` + `s3:GetObject` only)
- [ ] Set `CLOUDFLARE_WEBHOOK_SECRET` (different from R2 credentials!)
- [ ] Configure SendGrid/SES email credentials
- [ ] Set `SENTRY_DSN` for error monitoring
- [ ] Set `SENTRY_ENVIRONMENT=production`
- [ ] Run `python manage.py migrate`
- [ ] Run `python manage.py collectstatic`
- [ ] Start Celery worker: `celery -A app worker -l info`
- [ ] Configure Nginx with `client_max_body_size 5m`
- [ ] Enable HTTPS (Let's Encrypt / Cloudflare)
- [ ] Run `python manage.py test` to verify

### Sentry Integration

Sentry is initialized in `settings.py` when `SENTRY_DSN` is set. It captures:

- **Django exceptions** — 500 errors, unhandled exceptions
- **Celery task failures** — R2 upload errors, email send failures
- **Performance traces** — 20% sampling by default (configurable via `SENTRY_TRACES_SAMPLE_RATE`)
- **Breadcrumbs** — All log levels captured for debugging context
- **PII Protection** — `send_default_pii=False` disables default PII collection, and custom `before_send` / `before_breadcrumb` scrubbers redact authorization headers, cookies, tokens, emails, and inline bearer strings before telemetry leaves the process.
