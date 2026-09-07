---
title: Deployment
description: Production checklist, CI promotion gates, and observability notes for the PhotoBox platform.
---

## CI/CD promotion (required before merge)

PhotoBox uses a **promotion lane** (`.github/workflows/ci.yml`) and a separate **fortress soak** (`.github/workflows/ci-fortress.yml`).

- Branch protection on `development` / `staging` / `main` should require the **`promotion-gate`** check only (not every leaf job). That job inspects `needs.*.result` and fails on `failure` or `cancelled` — do not rely on bare `if: always()`.
- Fortress runs on a weeknight schedule / dispatch with `cancel-in-progress: false` so soak is never killed mid-flight and a red PR linter cannot cascade-skip ZAP.
- Staging promote: workflow `Deploy Staging` (`workflow_dispatch`) + GitHub Environment `staging`. It pulls the **same GHCR digest** CI built, optionally checks fortress freshness (&lt;7 days), then smokes health + anonymous `GET /api/galleries/g/1/` → JSON 404.
- **Daraja / live Safaricom STK is never on default CI.** Newman and ZAP forbid `daraja` paths. Daraja appears only as unit tests (hashed token + IP allowlist). Opt-in live sandbox is not part of promo.

Enable GitHub Dependabot security updates in the repo UI. Version bumps land on `development` via `.github/dependabot.yml` (grouped minors; Django/DRF majors ignored).

CodeQL runs in a separate workflow (`python` + `actions`). Do not make CodeQL a required check until one green soak.

## Production checklist

- Set `DEBUG=0` (never set `PHOTBOX_DAST` / `SCANNER` in production).
- Use a long, random `SECRET_KEY`.
- Configure `ALLOWED_HOSTS` for the production domain.
- Restrict `CORS_ALLOWED_ORIGINS` to trusted frontend hosts (exact origins only).
- Scope R2 IAM permissions to the minimum required object actions.
- Use a dedicated `CLOUDFLARE_WEBHOOK_SECRET`.
- Configure production email credentials.
- Set `SENTRY_DSN` and `SENTRY_ENVIRONMENT=production`.
- Run `python manage.py migrate` as a **one-shot job** (`wait_for_db && migrate --noinput`) — not on every HPA/app replica (`RUN_MIGRATIONS=0`).
- Run `python manage.py collectstatic`.
- Start Celery workers for image-processing **and** the dedicated `archive-zip` queue (`celery-archive`, prefetch=1, stop_grace_period ≥ soft time limit); start Celery Beat.
- Redis: `maxmemory-policy noeviction` with AOF on; set `CELERY_RESULT_EXPIRES` / `ignore_result` on media tasks.
- Liveness `GET /health/` (no DB); readiness `GET /api/health-check/`.
- Require `IP_HASH_SALT` (≥16 chars) when `DEBUG=False`.
- Apply R2 AbortIncompleteMultipartUpload lifecycle (`deploy/r2/`).
- After Cloudflare notification rules point at `/api/v1/ingestion/webhook/`, set `ENABLE_LEGACY_R2_WEBHOOK=0`.
- Enforce `client_max_body_size 5m` in Nginx.
- Enable HTTPS.
- Scale-out must pull an **immutable image digest** from GHCR (the digest `promotion-gate` certified), never `:latest`.
- Green `promotion-gate` before merge; green fortress freshness before staging promote.

See [`scripts/scale/README.md`](https://github.com/ALEX-MUTHOMI/PhotoBox-API/blob/development/scripts/scale/README.md) for envelope floors, Little’s Law pool caps, and attacker-proof HPA.

## Sentry integration

Sentry is enabled when `SENTRY_DSN` is present. The current posture is designed to be operationally useful without leaking obvious sensitive data:

- Django exceptions are captured automatically.
- Celery task failures are captured from worker execution.
- Performance traces are sampled from `SENTRY_TRACES_SAMPLE_RATE`.
- Breadcrumb scrubbers remove tokens, cookies, emails, and inline bearer secrets before export.
- Do **not** attach high-cardinality labels (`gallery_id`, `share_code`) to metrics — they explode series count under 100k+ galleries.

## Docs portal deployment

The Starlight site itself is a static build. A safe production path looks like this:

1. Build with `npm run check` and `npm run build` from `photobox-docs-site/`.
2. Publish the generated `dist/` directory to a static host or CDN-backed bucket.
3. Put the site behind HTTPS and a CDN cache.
4. Set `DOCS_SITE_URL` so canonical links and sitemap output are correct.
