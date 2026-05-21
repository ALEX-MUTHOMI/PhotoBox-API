# PHOTOBOX Production Verification Report

## Phase 0 Baseline

### Scope

This report is based on local repository inspection and local/Dockerized execution only. Source code and executable results are treated as authoritative. The external assessment `.docx` file was not readable during this pass because the file was locked by another process.

### Repository Layout

- Backend root contains Django app code under `app/`, Docker assets, scripts, GitHub Actions, and two documentation sites.
- Primary backend apps found in `INSTALLED_APPS`: `core`, `user`, `gallery`, `billing`, `checkout`, `ingestion`, `webhooks`.
- Legacy/compatibility models also exist in `core` (`Gallery`, `Image`) beside the newer event/scene/photo model set.

### Runtime Stack

| Area | Actual Baseline | Evidence |
|---|---|---|
| Python | Local venv `3.13.7`; Docker test runtime `3.12.13` | `venv\Scripts\python.exe --version`; `docker compose run --rm test ...` |
| Django | `4.0.10` | `venv\Scripts\python.exe -m pip show Django` |
| DRF | `3.13.1` | `venv\Scripts\python.exe -m pip show djangorestframework` |
| Celery | `5.6.3` locally; worker config from Django settings | `venv\Scripts\python.exe -m pip show celery`, [app/app/celery.py](/abs/path/c:/Project P/photobox-api/app/app/celery.py:1) |
| Database | PostgreSQL in runtime config; SQLite fallback only for tests | [app/app/settings.py](/abs/path/c:/Project P/photobox-api/app/app/settings.py:202), [docker-compose.yml](/abs/path/c:/Project P/photobox-api/docker-compose.yml:25) |
| Broker / cache | Redis | [app/app/settings.py](/abs/path/c:/Project P/photobox-api/app/app/settings.py:266), [docker-compose.yml](/abs/path/c:/Project P/photobox-api/docker-compose.yml:43) |
| Object storage | Cloudflare R2 | [app/app/settings.py](/abs/path/c:/Project P/photobox-api/app/app/settings.py:395) |
| CDN / delivery | Cloudinary fetch delivery | [app/app/settings.py](/abs/path/c:/Project P/photobox-api/app/app/settings.py:412), [app/gallery/models.py](/abs/path/c:/Project P/photobox-api/app/gallery/models.py:179) |
| Billing provider | Lemon Squeezy | [app/app/settings.py](/abs/path/c:/Project P/photobox-api/app/app/settings.py:418) |

### Actual Django Apps

- `core`: custom `User`, `Workspace`, legacy `Gallery` / `Image`, security helpers, signals, health check.
- `user`: registration, JWT login/refresh, Google social login, password reset, throttling.
- `gallery`: photographer event/scene/photo APIs, public/client gallery access, favorites, archive jobs, storage utilities, Celery tasks.
- `billing`: Lemon Squeezy webhook handling, subscription/quota ledger, audit log, dead-letter queue.
- `checkout`: plan listing and checkout session generation.
- `ingestion`: heavy-lane bulk manifest flow and one R2 webhook path.
- `webhooks`: second Cloudflare/R2 webhook path with separate tests.

### Actual API Route Map

| Area | Routes | Evidence |
|---|---|---|
| Health/docs | `/api/health-check/`, `/api/schema/`, `/api/docs/` | [app/app/urls.py](/abs/path/c:/Project P/photobox-api/app/app/urls.py:12) |
| Photographer auth | `/api/user/create/`, `/api/user/token/`, `/api/user/token/refresh/`, `/api/user/google/`, `/api/user/password-reset/`, `/api/user/password-reset/confirm/`, `/api/user/me/` | [app/user/urls.py](/abs/path/c:/Project P/photobox-api/app/user/urls.py:9) |
| Dashboard/event/scene | `/api/gallery/dashboard/`, router-backed `/api/gallery/events/`, `/api/gallery/scenes/` | [app/gallery/urls.py](/abs/path/c:/Project P/photobox-api/app/gallery/urls.py:35) |
| Fast Lane uploads | router-backed `/api/gallery/fast-lane/photos/`, explicit `/api/gallery/fast-lane/photos/<uuid:pk>/download-url/` | [app/gallery/urls.py](/abs/path/c:/Project P/photobox-api/app/gallery/urls.py:24) |
| Heavy Lane ingestion | `/api/v1/ingestion/bulk/` | [app/ingestion/urls.py](/abs/path/c:/Project P/photobox-api/app/ingestion/urls.py:4) |
| R2 webhook paths | `/api/v1/ingestion/webhook/` and `/api/v1/webhooks/cloudflare/r2/` | [app/ingestion/urls.py](/abs/path/c:/Project P/photobox-api/app/ingestion/urls.py:9), [app/webhooks/urls.py](/abs/path/c:/Project P/photobox-api/app/webhooks/urls.py:4) |
| Public/client gallery | `/api/galleries/<gallery_id>/`, magic link, guest access, favorites, archive, archive status, favorites archive/status | [app/gallery/client_urls.py](/abs/path/c:/Project P/photobox-api/app/gallery/client_urls.py:7) |
| Favorites summary | `/api/galleries/<gallery_id>/favorites-summary/` | [app/gallery/client_urls.py](/abs/path/c:/Project P/photobox-api/app/gallery/client_urls.py:28) |
| Billing/checkout | `/api/billing/webhook/`, `/api/billing/gallery/upload/`, `/api/checkout/plans/`, `/api/checkout/generate/` | [app/billing/urls.py](/abs/path/c:/Project P/photobox-api/app/billing/urls.py:7), [app/checkout/urls.py](/abs/path/c:/Project P/photobox-api/app/checkout/urls.py:4) |

### Test / Tooling Baseline

| Command | Result | Notes |
|---|---|---|
| `venv\Scripts\python.exe --version` | Passed | Local venv is Python `3.13.7`. |
| `venv\Scripts\python.exe -m pip show Django djangorestframework celery` | Passed | Django `4.0.10`, DRF `3.13.1`, Celery `5.6.3`. |
| `docker compose config -q` | Passed with warnings | Compose emitted repeated `The "iw" variable is not set` warnings; source still unknown. |
| `python manage.py check` | Failed in raw local env | `DEBUG` shell env was `release`; [app/app/settings.py](/abs/path/c:/Project P/photobox-api/app/app/settings.py:91) hard-casts `DEBUG` with `int()`. |
| `TESTING=1 DEBUG=0 ... python manage.py check` | Passed | `System check identified no issues (0 silenced).` |
| `TESTING=1 DEBUG=0 ... python manage.py makemigrations --check --dry-run` | Passed | `No changes detected`. |
| `venv\Scripts\python.exe -m pytest -q` | Failed | Local venv does not have `pytest` installed. |
| `venv\Scripts\python.exe -m flake8 --config .flake8 .` | Failed | Local venv does not have `flake8` installed. |
| `docker compose run --rm test flake8` | Passed | Lint output `0`. |
| `docker compose run --rm test security -x` | Passed | `87 passed` in `75.38s`. |
| `docker compose run --rm test celery` | Passed | `18 passed` in `78.69s`. |
| `docker compose run --rm test unit` | Failed | `20 passed, 264 errors` in `260.08s`; visible failures manifested as per-test `Timeout >60.0s`. |
| `docker compose run --rm test pytest core/tests/test_models.py::ModelTests::test_create_user_with_email_successful -x -vv` | Passed | Single isolated model test passed, which suggests the aggregate `unit` failure is order-dependent, cumulative, or environment-sensitive rather than universal breakage. |

### Immediate Baseline Findings

1. **Local environment boot is fragile**  
   The app crashes if `DEBUG` is any non-numeric truthy/falsy string because settings use `bool(int(...))`. In this shell, `DEBUG=release`, which prevented a normal `manage.py check`.

2. **Local dev tooling is incomplete**  
   The checked-in `venv` lacks `pytest` and `flake8`, so the local non-Docker workflow cannot reproduce CI-grade validation without additional setup.

3. **Dockerized quality gates are partially healthy**  
   Dedicated `security`, `celery`, and `flake8` lanes pass. The aggregate `unit` lane is unstable and currently unsuitable as a trustworthy release gate.

4. **There are dual R2 webhook entrypoints**  
   Both `ingestion` and `webhooks` expose Cloudflare/R2 webhook-style endpoints. This is a likely architecture ambiguity to verify in Phase 1.

5. **Production deploy config has visible drift from application routes/dependencies**  
   `docker-compose-deploy.yml` healthchecks `http://127.0.0.1:8000/health/`, but Django exposes `/api/health-check/`. The same deploy file configures Celery Beat with `django_celery_beat.schedulers:DatabaseScheduler`, but `django_celery_beat` is not present in `requirements.txt`.

### Phase 0 Conclusion

Phase 0 establishes that the repository is executable in Docker and that major security and async test lanes currently pass. It also establishes that:

- the local developer path is brittle,
- the broad `unit` regression suite is not reliable,
- there is already source-level evidence of legacy/canonical path overlap,
- and the deployment config cannot be assumed correct without further verification.

The next phases should focus first on architecture truth mapping and on explaining the `unit` suite instability, because both are likely to uncover canonical-vs-legacy behavior drift.
