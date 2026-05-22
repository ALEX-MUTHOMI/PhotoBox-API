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

## Phase 1 Architecture Truth Map

### 1. Tenant Boundary

**Canonical tenant object:** `core.Workspace`.

**Evidence chain**

- `core.models.Workspace` is the photographer-owned root object.
- `gallery.models.Event.workspace -> Workspace`
- `gallery.models.Scene.event -> Event.workspace`
- `gallery.models.Photo.scene -> Scene.event -> Event.workspace`
- `gallery.models.GalleryAccessSession.gallery -> Event.workspace`
- `gallery.models.FavoriteSelection.session -> GalleryAccessSession.gallery -> Event.workspace`
- `gallery.models.FavoriteSelection.photo -> Photo.scene.event.workspace`
- `gallery.models.GalleryArchiveJob.gallery -> Event.workspace`
- `checkout.models.CheckoutSession.user -> core.User`; billing state then hangs off `billing.models.Subscription.user`, not directly off `Workspace`.

**Endpoints that enforce the chain correctly**

- `gallery.views.EventViewSet.get_queryset()` restricts events to `workspace__user=request.user`.
- `gallery.views.SceneViewSet.get_queryset()` restricts scenes to `event__workspace__user=request.user`; `perform_create()` rechecks `event.workspace.user`.
- `gallery.views.PhotoFastLaneViewSet.perform_create()` rechecks `scene.event.workspace.user == request.user` before accepting `scene` from the client.
- `ingestion.serializers.BulkManifestSerializer.validate_scene_id()` and `ingestion.views.BulkIngestionView._phase2_commit()` both recheck `scene.event.workspace.user`.
- `gallery.client_views.*` consistently scope gallery access through `GalleryAccessSession`, gallery role, gallery expiry, scene/photo visibility, and published status.
- `gallery.views.PhotographerFavoritesSummaryView` scopes favorites to `Event.workspace__user=request.user`.

**Endpoints / paths that remain risky or ambiguous**

- `billing.views.GalleryUploadView` is a legacy quota endpoint keyed only to `Subscription.user`. It does not participate in the `Workspace -> Event -> Scene -> Photo` ownership chain and is therefore not a safe canonical upload surface for the gallery domain.
- `core` still contains legacy `Gallery` / `Image` models beside `gallery.Event` / `Scene` / `Photo`. They are not the active route surface, but they preserve a second vocabulary for tenancy and media ownership.
- `gallery.views.PhotoFastLaneViewSet.download_url()` deliberately accepts both photographer JWT and gallery cookie principals. The branching logic is source-backed and appears intentional, but it is a boundary-sensitive path because it merges two auth domains in one action.

### 2. Quota Ledger

**Actual state:** there are two ledgers.

| Ledger | Source of truth in code | Used by | Notes |
|---|---|---|---|
| Workspace storage ledger | `core.models.Workspace.storage_used_bytes` / `storage_limit_bytes` | Canonical gallery Fast Lane and Heavy Lane | Locked with `select_for_update()` inside `transaction.atomic()` in gallery and ingestion flows. |
| Subscription storage ledger | `billing.models.Subscription.storage_used_bytes` / `storage_limit_bytes` | Legacy `billing.views.GalleryUploadView` and photographer dashboard display | Creates domain drift because dashboard and billing can disagree with gallery ingestion. |

**Canonical path behavior**

- Fast Lane reserves quota in `gallery.views.PhotoFastLaneViewSet.perform_create()` by locking the `Workspace` row with `select_for_update()` and incrementing `storage_used_bytes` before the Celery handoff.
- Heavy Lane reserves quota in `ingestion.views.BulkIngestionView._phase2_commit()` using `transaction.atomic()` plus `Workspace.objects.select_for_update(nowait=True)`.
- Failed/abandoned fast-lane uploads refund quota in `gallery.tasks._handle_abandoned_upload()` using `Greatest(0, F(...) - refund_bytes)`.
- Heavy Lane persists declared sizes at commit time, but there is no equivalent periodic reconciliation job in `ingestion` to reclaim stale reservations if the upload never finishes.

**Risk**

- `Workspace` is the operational ledger for real gallery ingestion.
- `Subscription` is still exposed to product surfaces and a legacy upload endpoint.
- The repo therefore has dual quota concepts, not one canonical quota ledger.

### 3. Upload Flows

**Fast Lane actual state machine**

1. Photographer authenticates with JWT.
2. `gallery.views.PhotoFastLaneViewSet.perform_create()` validates ownership, size, Pillow image structure, decompression-bomb handling, allowed formats, trailing payload absence, and sanitized filename.
3. The view reserves bytes on `Workspace`.
4. A `gallery.Photo` row is created with the uploaded `image_file`, `original_filename`, `file_size_bytes`, and default `PENDING` status.
5. `gallery.tasks.process_fast_lane_asset.delay(photo_id)` is dispatched.
6. `gallery.tasks.process_fast_lane_asset()` does not upload the local `image_file` to R2 itself. It checks whether `photo.r2_object_key` already exists in R2, self-heals to `READY` if present, or marks the upload abandoned and refunds quota if no R2 object is found.
7. If an R2 original exists, `generate_photo_web_derivative()` can create `web_r2_object_key` and enable Cloudinary fetch delivery.

**Heavy Lane actual state machine**

1. Photographer authenticates with JWT.
2. `ingestion.views.BulkIngestionView` applies `HeavyLaneTicketThrottle`.
3. `BulkManifestSerializer` validates `scene_id`, per-file filename/media/size constraints, duplicate `client_reference_id`, and declared total size.
4. `_phase1_prepare_assets()` generates tenant-scoped object keys under `raw/tenant_{user_id}/scene_{scene_id}/...` and presigned POST conditions.
5. `_phase2_commit()` locks the `Workspace`, rechecks scene ownership, reserves quota, and bulk-creates `Photo` rows with `r2_object_key` plus `PENDING`.
6. `ingestion.views.R2WebhookView` transitions matching rows to `READY` after HMAC/timestamp validation, declared-size verification, and row locking, then dispatches derivative generation.

**Delivery assumptions**

- `gallery.models.Photo.delivery_url` prefers `web_r2_object_key` and constructs a Cloudinary fetch URL from the R2 public domain.
- `gallery.models.Photo.download_url` delegates to `gallery.storage.generate_r2_presigned_get_url()`.
- `gallery.storage` clamps presigned download TTL to 60 seconds even though some serializer comments still describe longer expiry.

**Stale-state handling**

- Fast Lane has an explicit refund path for abandoned uploads.
- Heavy Lane has webhook idempotency and ghost-key tolerance, but no source-backed periodic cleanup task for permanently pending rows.

### 4. Webhook Flows

| Domain | Path | Actual behavior | Risk |
|---|---|---|---|
| Canonical R2 asset webhook | `ingestion.urls -> /api/v1/ingestion/webhook/` | `ingestion.views.R2WebhookView` validates HMAC and timestamp through `core.security`, tolerates ghost keys with `200`, locks matching photo rows, enforces size match, and skips already-READY assets. | Best current implementation. |
| Duplicate R2 asset webhook | `webhooks.urls -> /api/v1/webhooks/cloudflare/r2/` | `webhooks.views.CloudflareWebhookView` performs a second, similar Cloudflare/R2 flow with separate tests and slightly different structure. | Dangerous ambiguity and long-term drift risk. |
| Billing webhook | `billing.urls -> /api/billing/webhook/` | `billing.views.WebhookReceiverView` validates primary/secondary Lemon Squeezy HMAC, rejects empty-secret bypass, rejects oversize payloads, parses JSON, and hands work to `process_lemon_squeezy_webhook.delay()`. | Stronger than docs implied, but no timestamp freshness window at the HTTP layer. |

**Replay / idempotency**

- Billing replay protection is implemented in `billing.tasks.process_lemon_squeezy_webhook()` via `ProcessedWebhook.event_id` plus a payload hash. This is good and source-backed.
- R2 handlers are idempotent at the row-state level because READY rows are skipped.
- Neither R2 path has a durable event ledger model; idempotency is inferred from current asset state, not from a stored webhook event registry.

**Dead-letter behavior**

- Billing task sends unprocessable webhook payloads to `billing.models.DeadLetterQueue`.
- No equivalent dead-letter queue exists for the R2 webhook domain.

### 5. Auth Domains

| Domain | Mechanism | Evidence | Boundary |
|---|---|---|---|
| Photographer API auth | JWT access token + HttpOnly refresh cookie | `user.views.EnterpriseTokenObtainPairView`, `CookieTokenRefreshView` | Canonical for dashboard, event, scene, Fast Lane, billing, checkout, ingestion. |
| Refresh flow | Cookie-backed `refresh` token | `user.views.CookieTokenRefreshView` | Rotates via SimpleJWT blacklist configuration. |
| Gallery-scoped auth | Separate HS256 gallery cookie JWT | `gallery.client_auth.GalleryCookieJWTAuthentication` | Canonical for public/client gallery actions, favorites, archive status. |
| Magic link | Single-use hashed token stored in DB | `gallery.client_views.GalleryMagicLinkRequestView`, `GalleryMagicLinkConsumeView` | Creates `GalleryAccessSession`, then gallery cookie. |
| Guest access | Session row + gallery cookie | `gallery.client_views.GalleryGuestAccessView` | Limited to guest role and published/non-expired galleries. |
| Password reset | Django token generator | `user.views.PasswordResetRequestView`, `PasswordResetConfirmView` | No extra replay ledger beyond Django token semantics. |
| Social login | Google via allauth / dj-rest-auth | `user.urls`, `settings.py` | Third-party trust boundary exists but was not re-exercised in this phase. |

### 6. Async System

| Task area | Source | Behavior |
|---|---|---|
| Fast Lane processing | `gallery.tasks.process_fast_lane_asset` | Retries, self-heals READY rows, refunds abandoned quota, dispatches derivative generation. |
| Web derivative generation | `gallery.tasks.generate_photo_web_derivative` | Pulls original from R2, applies optional watermark, uploads derived WebP to R2. |
| Archive generation | `gallery.tasks.build_gallery_archive` | Streams authorized READY photos from R2 into a ZIP and writes archive back to R2. |
| Billing webhook processing | `billing.tasks.process_lemon_squeezy_webhook` | Idempotent processing, subscription updates, checkout session completion, DLQ fallback. |
| Gallery publish notification | `gallery.views.EventViewSet.perform_update()` -> `gallery.notifications.send_gallery_ready_email.delay()` | Async email dispatch on first publish transition. |

**Retry / idempotency observations**

- Billing task is explicitly idempotent and row-locked.
- Gallery tasks rely more on row state than on an external idempotency ledger.
- No `django_celery_beat`-backed periodic schedule was found in source; the old deploy reference was stale.

### 7. Canonical vs Legacy Path Table

| Domain | Canonical path | Legacy / duplicate path | Evidence files | Risk | Recommended action |
|---|---|---|---|---|---|
| Gallery uploads | `gallery.views.PhotoFastLaneViewSet`, `ingestion.views.BulkIngestionView` | `billing.views.GalleryUploadView` | `app/gallery/views.py`, `app/ingestion/views.py`, `app/billing/views.py` | High | Keep legacy path documented as non-canonical; do not use for new frontend flows. |
| Asset webhook | `/api/v1/ingestion/webhook/` | `/api/v1/webhooks/cloudflare/r2/` | `app/ingestion/urls.py`, `app/webhooks/urls.py` | High | Consolidate to one R2 webhook path after explicit deprecation plan. |
| Gallery domain models | `gallery.Event`, `gallery.Scene`, `gallery.Photo` | `core.Gallery`, `core.Image` | `app/gallery/models.py`, `app/core/models.py` | Medium | Mark `core` media models as legacy in docs and admin; verify no live codepaths depend on them before removal. |
| Quota ledger | `Workspace.storage_*` | `Subscription.storage_*` | `app/core/models.py`, `app/billing/models.py`, `app/gallery/views.py`, `app/ingestion/views.py` | High | Collapse product/UI reads onto the workspace ledger or add deterministic reconciliation. |
| Health probe | `/api/health-check/` with proxy-aware HTTPS header | old `/health/` and plain-HTTP internal probes | `app/app/urls.py`, `docker-compose.yml`, `docker-compose-deploy.yml`, `app/app/settings.py` | High | Fixed in this pass. |
| Celery Beat | plain `celery -A app beat` | old `django_celery_beat.schedulers:DatabaseScheduler` reference | `docker-compose-deploy.yml`, `app/app/settings.py` | Medium | Fixed stale scheduler reference; only reintroduce DB scheduler with real dependency and config. |

### 8. Dangerous Ambiguity List

- Dual quota concepts: `Workspace` is the real upload ledger while `Subscription` still drives legacy upload and some dashboard display.
- Dual R2 webhook namespaces: `ingestion` and `webhooks` both implement Cloudflare/R2 reconciliation.
- Stale core models: `core.Gallery` / `core.Image` coexist with `gallery.Event` / `Photo`.
- Deploy healthcheck drift: fixed in this pass, but the drift was real and blocked production-like startup verification.
- Missing `django_celery_beat` dependency vs old deploy reference: fixed by removing the stale scheduler invocation.
- `DEBUG` parsing fragility: fixed in this pass with explicit boolean validation.
- Unit-suite instability: traced to orchestration/shared-state interference, not a currently reproducible app-logic deadlock.

## Phase 1B Unit Suite Instability Analysis

### Reproduction

- Historical failing command from Phase 0: `docker compose run --rm test unit`
- Historical result: `20 passed, 264 errors`, with repeated `Timeout >60.0s`

### Follow-up investigation

- `docker compose run --rm test unit --maxfail=1 -vv` was rerun in isolation and passed: `284 passed in 747.35s`.
- `docker compose run --rm test pytest core/tests/test_settings_boot.py -vv` passed after the settings fix.
- Targeted suites (`flake8`, `security -x`, `celery`) also passed in isolation.

### First credible root cause

The aggregate unit lane was not failing deterministically at the application level. The strongest evidence points to **shared Docker/DB interference across concurrent or overlapping test invocations**:

- Earlier compose operations produced container / network conflicts and orphan warnings.
- A successful full run still emitted a PostgreSQL teardown warning that `test_devdb` was being accessed by other sessions.
- The test service uses shared `db` and `redis` services and a shared pytest-django database name, so overlapping `docker compose run --rm test ...` commands can collide on the same infrastructure and leak sessions into teardown.

### Conclusion

- **This is a real CI reliability issue.**
- It is **not** currently explained by a single broken test, deadlocked fixture, or reproducible app-level timeout.
- CI should fail the unit gate if the suite is executed against shared infrastructure in parallel.
- CI should run the unit lane **serially and in isolation** for this compose project, or use a per-job project name / isolated test DB.

### Recommended fix

1. Never run multiple `docker compose run --rm test ...` jobs against the same compose project and database simultaneously.
2. Give each CI job its own compose project namespace or its own database name if unit, celery, and smoke run concurrently.
3. Keep `pytest-timeout` enabled; do not paper over the original symptom by increasing timeouts.
4. Add a CI note that the historical timeout cascade was environmental/orchestration drift, not a proof that the unit suite is healthy under concurrency.

## CI/CD Protocol Proposal

### Current state

- Current GitHub Actions workflow is a single broad job in `.github/workflows/checks.yml`.
- It is Docker-first, but it is not staged the way a production verification pipeline should be.
- There is **no** `pyproject.toml` or `poetry.lock` in the repository, so a true Poetry gate cannot pass today.

### Recommended gate order

`validate -> lint -> security -> django-smoke -> unit -> celery -> integration -> toxiproxy -> docker-build`

### Gate status from this pass

| Gate | Command | Result | Notes |
|---|---|---|---|
| validate | `docker compose config -q` | pass | Base compose syntax is valid. |
| validate | `docker compose -f docker-compose.yml -f docker-compose.toxiproxy.yml config -q` | pass | Toxiproxy compose overlay is valid. |
| validate | `poetry check` | fail | Blocked because `pyproject.toml` and `poetry.lock` do not exist. |
| lint | `docker compose run --rm test flake8` | pass | Passed after settings / CI-script changes. |
| security | `docker compose run --rm test security -x` | pass | Phase 0 baseline: `87 passed`. |
| django-smoke | `docker compose run --rm -v <repo>/scripts:/scripts --entrypoint sh test /scripts/ci/django_smoke.sh` | pass | New gate exercised end to end. |
| unit | `docker compose run --rm test unit --maxfail=1 -vv` | pass | Serial isolated rerun passed; historical failures traced to shared-state interference. |
| celery | `docker compose run --rm test celery` | pass | Phase 0 baseline: `18 passed`. |
| integration | not separately implemented yet | fail | Still folded into Dockerized test flows; no dedicated authenticated API integration lane exists yet. |
| toxiproxy | `docker compose -f docker-compose.yml -f docker-compose.toxiproxy.yml --profile toxiproxy run --rm -v <repo>/scripts:/scripts toxiproxy-test` | pass | Postgres and Redis latency/cut/recovery scenarios executed. |
| docker-build | not separately run for `DEV=false` production image in this phase | fail | Production image build still needs a dedicated gate. |

### Proposed CI stages

- `validate`
  - `poetry --version`
  - `poetry check`
  - lockfile consistency check
  - `docker compose config -q`
  - `python manage.py check`
  - `python manage.py makemigrations --check --dry-run`
- `lint`
  - Dockerized `flake8`
- `security`
  - Existing `security -x`
  - optional dependency audit after Poetry migration
- `django-smoke`
  - `scripts/ci/django_smoke.sh`
- `unit`
  - Dockerized `test unit`, isolated per job
- `celery`
  - Dockerized `test celery`
- `integration`
  - dedicated API flow against Postgres/Redis with mocks for R2/Cloudinary/Lemon
- `toxiproxy`
  - `docker-compose.toxiproxy.yml` overlay plus `scripts/ci/toxiproxy_smoke.sh`
- `docker-build`
  - production `DEV=false` image build and import smoke

## Poetry Protocols

### Actual repository truth

- There is **no** `pyproject.toml`.
- There is **no** `poetry.lock`.
- Dependency management is requirements-based:
  - `requirements.txt` for runtime
  - `requirements.dev.txt` for dev/test tools
  - `app/tests/requirements-test.txt` as a second, partially overlapping test dependency file

### Supported Python version

- **Recommended supported version:** Python `3.12`
- Evidence:
  - Dockerfile uses `python:3.12-slim-bookworm`
  - Dockerized smoke, unit, celery, lint, and toxiproxy runs all used Python `3.12.13`
  - Local checked-in venv is `3.13.7`, but the repo is not validated against it and previously lacked required tools

### Protocol recommendation

1. Adopt Poetry only after creating `pyproject.toml` and `poetry.lock`.
2. Declare `python = ">=3.12,<3.13"` unless the full suite is re-verified on 3.13.
3. Split groups into:
   - main/runtime
   - `dev` for lint/test tools
   - `ci` or `security` for audit/scanning tools
4. Make CI fail if `pyproject.toml` changes without `poetry.lock` refresh.
5. Do not use the checked-in local `.venv` as evidence of support.

### Blocking issue

Poetry discipline is **designed but not fully implementable yet** because the repository still lacks Poetry metadata. That is a release-process blocker for deterministic dependency validation, not a code-runtime blocker.

## Django Smoke Gate

### Implemented artifacts

- `scripts/ci/django_smoke.sh`
- `app/core/tests/test_settings_boot.py`

### What the smoke gate now proves

- settings import succeeds under controlled env
- invalid `DEBUG=release` fails clearly
- `manage.py check` succeeds
- `manage.py check --deploy` can run under `DEBUG=0`
- URLConf imports
- Celery app imports
- migrations are current
- `/api/health-check/` exists and is reachable under proxy-aware HTTPS semantics

### Real issues found and fixed while building the gate

- `DEBUG` previously crashed with `ValueError` because settings hard-cast through `int()`.
- Internal health checks were incompatible with `SECURE_SSL_REDIRECT` in `DEBUG=0`.
- Deploy compose used a stale `/health/` path and a stale `django_celery_beat` scheduler invocation.

## Toxiproxy Gate

### Implemented artifacts

- `docker-compose.toxiproxy.yml`
- `scripts/ci/toxiproxy_smoke.sh`

### Executed scenarios

- baseline DB query through `postgres_proxy`
- baseline Redis cache roundtrip through `redis_proxy`
- PostgreSQL latency injection
- PostgreSQL connection cut
- PostgreSQL recovery after toxic removal
- Redis latency injection
- Redis connection cut
- Redis recovery after toxic removal

### Design notes

- The toxiproxy admin API in the upstream image only bound reliably to loopback for this environment.
- The working fix was to run `toxiproxy-test` in the proxy container's network namespace and talk to `127.0.0.1`.
- This gate currently proves bounded failure/recovery for DB and Redis primitives. It does **not yet** prove no quota corruption or no false READY transition in a real upload workflow under fault injection. That remains P1 hardening work.

## Production Readiness Gaps

## Executive Summary

- Overall rating: **Not production ready**
- Recommended launch gate: **Production ready after critical fixes**
- Reason: startup and resilience gates now run, but the repository still has unresolved architectural ambiguity in quota and webhook ownership, lacks deterministic Poetry lock discipline, and does not yet have a dedicated production-image or authenticated integration gate.

### Top 5 launch blockers

1. Dual quota ledgers (`Workspace` vs `Subscription`) can produce conflicting entitlement decisions.
2. Duplicate R2 webhook paths (`ingestion` and `webhooks`) create reconciliation drift risk.
3. No Poetry metadata or lockfile exists, so dependency validation cannot be deterministic under the requested protocol.
4. Production `DEV=false` image build/import smoke was not yet a passing gate in this phase.
5. No dedicated authenticated integration lane exists for real API flow verification against mocked third-party services.

### Top 5 hardening recommendations

1. Collapse product-facing storage enforcement onto a single ledger.
2. Deprecate one R2 webhook namespace and retain one canonical reconciliation path.
3. Add Poetry metadata and lock discipline, then make `validate` fail on drift.
4. Add a production-image CI stage that boots the built image under `DEBUG=0`.
5. Extend the Toxiproxy lane from primitive DB/Redis checks into upload, quota, and billing state assertions.

### Launch blockers

1. Dual quota ledger remains unresolved: `Workspace` is the real ingest ledger while `Subscription` is still exposed in product code.
2. Dual R2 webhook paths remain live and duplicative.
3. Poetry / lockfile discipline does not exist yet; dependency validation is not deterministic outside Docker requirements files.
4. No dedicated production `DEV=false` docker-build gate was run in this phase.
5. No dedicated authenticated integration lane exists yet for real end-to-end API verification against mocked storage/billing providers.

### Hardening items

- Add a single canonical R2 webhook path and deprecate the other.
- Collapse dashboard / upload quota reads onto one ledger.
- Add DRF spectacular extensions or explicit serializer/schema annotations for custom auth and APIViews.
- Add a real integration test that exercises gallery upload state under DB/Redis disruption through Toxiproxy.
- Replace the stale/incorrect local `Makefile` assumptions over time; only the new `ci-*` targets align with current Dockerized verification.

## Changes Made

| File | Change | Risk addressed |
|---|---|---|
| `app/app/settings.py` | Replaced `bool(int(...))` with strict boolean parsing; added `SECURE_PROXY_SSL_HEADER` in `DEBUG=0` mode | Boot fragility, invalid env handling, proxy-aware HTTPS health checks |
| `docker-compose.yml` | Healthcheck now sends `X-Forwarded-Proto: https` to `/api/health-check/` | Internal health probe compatibility |
| `docker-compose-deploy.yml` | Fixed health route; removed stale `django_celery_beat` scheduler reference; made health probe proxy-aware | Deploy drift / missing dependency path |
| `scripts/ci/django_smoke.sh` | Added deterministic smoke gate with explicit env isolation | Startup verification |
| `docker-compose.toxiproxy.yml` | Added Toxiproxy overlay and corrected namespace/listener design | Resilience verification |
| `scripts/ci/toxiproxy_smoke.sh` | Added DB/Redis latency/cut/recovery gate | Dependency failure verification |
| `app/core/tests/test_settings_boot.py` | Added DEBUG parsing regression tests | Prevents settings regression |
| `Makefile` | Added Dockerized `ci-*` targets for validate/lint/security/smoke/unit/celery/toxiproxy | Developer/CI ergonomics |

## Tests Run

| Command | Result | Notes |
|---|---|---|
| `docker compose config -q` | Pass | Base compose syntax valid. |
| `docker compose -f docker-compose.yml -f docker-compose.toxiproxy.yml config -q` | Pass | Toxiproxy overlay syntax valid. |
| `docker compose run --rm test pytest core/tests/test_settings_boot.py -vv` | Pass | `2 passed`. |
| `docker compose run --rm test flake8` | Pass | Lint clean after changes. |
| `docker compose run --rm -v <repo>/scripts:/scripts --entrypoint sh test /scripts/ci/django_smoke.sh` | Pass | Smoke gate completed end to end. |
| `docker compose -f docker-compose.yml -f docker-compose.toxiproxy.yml --profile toxiproxy run --rm -v <repo>/scripts:/scripts toxiproxy-test` | Pass | Postgres and Redis fault-injection scenarios completed. |
| `docker compose run --rm test unit --maxfail=1 -vv` | Pass | Prior isolated rerun passed all tests; used for RCA. |

## Remaining Launch Blockers

- Not production ready yet.
- Main blockers are architectural ambiguity, not raw startup failure:
  - dual quota ledgers
  - duplicate R2 webhook paths
  - no Poetry/lockfile source of truth
  - no dedicated production image gate
  - no dedicated authenticated integration gate

## Recommended Launch Gate

- Classification: **Not production ready**
- Upgrade path: **Production ready after critical fixes**

## Next Sprint Plan

### P0 security / correctness

- Choose one canonical quota ledger and remove product-facing ambiguity.
- Choose one canonical R2 webhook path and deprecate the duplicate.
- Add upload/billing fault-injection tests that assert no false READY or quota corruption.

### P1 production reliability

- Add a dedicated production `DEV=false` docker-build and import smoke gate.
- Create a real integration stage for authenticated photographer and gallery-client flows with mocked third parties.
- Isolate compose project names or DB names per CI job to prevent shared-state test interference.

### P2 cleanup and documentation

- Document `core` media models as legacy or remove them after usage proof.
- Align docs/comments with actual Fast Lane behavior and actual presigned URL TTL.
- Replace the old single-job GitHub Actions workflow with staged gates.

### P3 future hardening

- Add DRF spectacular auth/schema extensions for gallery-cookie auth.
- Add object-storage proxying for upload/webhook flows under Toxiproxy or a local S3-compatible emulator.
- Add observability assertions for structured, non-sensitive error logging in failure-path tests.

