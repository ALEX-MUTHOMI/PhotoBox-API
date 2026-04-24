---
title: Testing
description: Test execution, suite coverage, and data seeding guidance for PhotoBox.
---

## Run all tests

```bash
cd app/
python manage.py test gallery ingestion webhooks --verbosity=2
```

## Test suite map

| Suite | File | Coverage |
| --- | --- | --- |
| Fast Lane API | `gallery/tests/test_fastlane_api.py` | Upload acceptance, quotas, image validation, decompression guards, tenant isolation |
| R2 webhook | `ingestion/tests/test_r2_webhook.py` | HMAC verification, replay handling, ghost keys, quarantine, idempotency |
| Legacy webhook | `webhooks/tests/test_cloudflare.py` | Signature validation, payload tampering, and action filtering |
| Ingestion views | `ingestion/tests/test_views.py` | Manifest validation, ticket generation, and quota locking |

## Seeder commands

```bash
python manage.py seed_db
python manage.py seed_db --flush
python manage.py seed_db --flush --workspace-count 5 --events-per-workspace 10 --scenes-per-event 10 --photos-per-scene 25 --batch-size 500
```

## Seeder characteristics

- Creates demo photographers, events, scenes, and gallery assets.
- Uses `bulk_create()` in bounded batches to keep larger seeds practical.
- Recomputes quota with aggregate queries instead of loading full tables into memory.
