# PhotoBox Security Red-Team Matrix

Date: 2026-05-23

Scope: Phase B strict TDD red-team sweep for PhotoBox API. PhotoBox remains a photography SaaS covering photographer accounts, workspaces, events, scenes, media uploads, public/client galleries, favorites, archive jobs, checkout, billing, webhooks, Cloudflare R2, Cloudinary delivery, Celery/Redis, PostgreSQL, and Lemon Squeezy.

Boundary: No Darasa domain logic or terminology is in scope.

## Inspected Code Surface

| Area | Files Inspected |
|---|---|
| Root routing and settings | `app/app/urls.py`, `app/app/settings.py` |
| Photographer auth | `app/user/urls.py`, `app/user/views.py`, `app/user/serializers.py`, `app/user/password_reset_serializers.py`, `app/user/adapters.py` |
| Dashboard gallery APIs | `app/gallery/urls.py`, `app/gallery/views.py`, `app/gallery/serializers.py`, `app/gallery/models.py` |
| Client gallery APIs | `app/gallery/client_urls.py`, `app/gallery/client_views.py`, `app/gallery/client_auth.py`, `app/gallery/client_permissions.py`, `app/gallery/client_serializers.py` |
| Storage and media workers | `app/gallery/storage.py`, `app/gallery/tasks.py`, `app/gallery/filename_utils.py` |
| Heavy Lane ingestion | `app/ingestion/urls.py`, `app/ingestion/views.py`, `app/ingestion/serializers.py`, `app/ingestion/tasks.py` |
| Webhooks | `app/webhooks/urls.py`, `app/webhooks/views.py`, `app/billing/views.py`, `app/billing/tasks.py` |
| Billing and checkout | `app/billing/urls.py`, `app/billing/models.py`, `app/billing/serializers.py`, `app/checkout/urls.py`, `app/checkout/views.py`, `app/checkout/serializers.py` |
| Shared security helpers | `app/core/security.py`, `scripts/ci/secret_hygiene.py`, `scripts/ci/env_sanity.py`, `scripts/ci/bandit_redacted.py` |
| Existing test coverage | `app/*/tests/`, `tests/smoke/`, `tests/integration/`, `tests/resilience/` |

## Endpoint And Trust-Boundary Matrix

| Route/Service | Actor | Auth Domain | Tenant Boundary | Data Touched | Expected Protection | Existing Tests | Risk |
|---|---|---|---|---|---|---|---|
| `GET /health/`, `GET /api/health-check/` | Anonymous / load balancer | anonymous/public access | None | Health response | No secrets, no DB-heavy behavior, deploy path alignment | `tests/smoke/test_healthcheck.py`, `core/tests/test_health_check.py` | Low |
| `GET /api/schema/`, `GET /api/docs/` | Anonymous / developer | anonymous/public access | None | OpenAPI schema/docs | No secret examples, no internal-only routes documented as public | Smoke only; deeper schema auth review pending | Medium |
| `POST /api/user/create/` | Anonymous external attacker | anonymous/public access | New photographer account | User, Workspace, Subscription signal | Turnstile check, Gmail alias normalization, terms acceptance, registration throttling, no privilege field mass assignment | `user/tests/test_user_api.py` | Medium |
| `POST /api/user/token/` | Photographer | photographer dashboard auth | User account | JWT access and refresh cookie | Generic errors, active-user check through auth backend, refresh cookie HttpOnly/Secure, brute-force throttle | `user/tests/test_user_api.py`, `core/tests/test_auth_jwt.py` | High |
| `POST /api/user/token/refresh/` | Photographer | photographer dashboard auth | User account/session | Refresh token, access token, refresh cookie | HttpOnly cookie extraction, rotation, blacklist after rotation, expired/reused refresh rejected | `core/tests/test_auth_jwt.py` | High |
| `POST /api/user/google/` | External OAuth user | photographer dashboard auth / OAuth | User account | Social login identity | Verified email required, blind account takeover blocked, existing linked provider allowed only when linked | `user/tests/test_social_adapter.py`, `user/tests/test_user_api.py` | High |
| `POST /api/user/password-reset/` | Anonymous external attacker | anonymous/public access | User account | Password reset email | Generic response for existing/non-existing email, rate limiting, no account enumeration | `user/tests/test_password_reset.py` | High |
| `POST /api/user/password-reset/confirm/` | Anonymous token holder | anonymous/public access | User account | Password reset token/password | Valid uid/token required, active user required, Django password validators, token replay should fail after password change | `user/tests/test_password_reset.py` | High |
| `GET/PATCH /api/user/me/` | Photographer | photographer dashboard auth | User account | User profile, password, terms metadata | Auth required, password change requires old password, billing fields read-only | `user/tests/test_user_api.py` | Medium |
| `GET/POST /api/gallery/events/` | Photographer | photographer dashboard auth | Workspace | Event/gallery metadata | Queryset scoped to `workspace__user`, workspace derived from authenticated user on create, payload workspace ignored | `gallery/tests/test_tenant_isolation.py`, `gallery/tests/test_events_api.py` | High |
| `GET/PATCH/DELETE /api/gallery/events/{id}/` | Photographer | photographer dashboard auth | Workspace/Event | Event/gallery metadata | Object lookup through scoped queryset, no cross-tenant read/update/delete, generic not found behavior preferred | `gallery/tests/test_tenant_isolation.py` | High |
| `GET/POST /api/gallery/scenes/` | Photographer | photographer dashboard auth | Workspace/Event | Scene metadata | Queryset scoped through event workspace, create rejects event owned by another user | `gallery/tests/test_tenant_isolation.py`, `gallery/tests/test_events_api.py` | High |
| `GET/PATCH/DELETE /api/gallery/scenes/{id}/` | Photographer | photographer dashboard auth | Workspace/Event/Scene | Scene metadata | Scoped queryset, update strips event reassignment, no cross-tenant moves | `gallery/tests/test_tenant_isolation.py` | High |
| `GET/POST /api/gallery/fast-lane/photos/` | Photographer | photographer dashboard auth | Workspace/Event/Scene/Photo | Photo rows, quota ledger, uploaded file | JWT photographer-only permission, scene ownership check, magic-byte validation, quota lock, status starts PENDING, no real provider call in web thread | `gallery/tests/test_fastlane_api.py`, `app/tests/test_api_upload.py`, `gallery/tests/test_asset_hardening.py` | Critical |
| `GET/DELETE /api/gallery/fast-lane/photos/{id}/` | Photographer | photographer dashboard auth | Workspace/Event/Scene/Photo | Photo metadata/quota | Queryset scoped to workspace owner, delete refunds quota atomically | `gallery/tests/test_tenant_isolation.py`, `gallery/tests/test_fastlane_api.py` | High |
| `GET /api/gallery/fast-lane/photos/{id}/download-url/` | Photographer or scoped gallery client | photographer dashboard auth and client/gallery-scoped auth | Workspace/Event/Photo/Gallery session | R2 presigned GET URL | Authorization before URL generation, gallery token scope/session revalidated, READY-only, short TTL | `gallery/tests/test_download_authorization.py`, `gallery/tests/test_download_workflows.py`, `gallery/tests/test_presigned_url_security.py` | Critical |
| `GET /api/galleries/{gallery_id}/` | Gallery client/guest | client/gallery-scoped auth | Gallery/session | Published gallery payload, scenes, photos | Published/non-expired gallery only, JWT gallery scope enforced, role-based visibility filter, READY-only photos | `gallery/tests/test_dual_lane_auth.py` | Critical |
| `POST /api/galleries/{gallery_id}/magic-link/` | Anonymous client | anonymous/public access | Gallery/client allowlist | Magic link token/email | Published gallery required, allowlist check, token stored hashed, generic response, send only if allowlisted | `gallery/tests/test_dual_lane_auth.py` | High |
| `POST /api/galleries/magic-link/consume/` | Anonymous token holder | anonymous/public access | Gallery/client session | Magic link, gallery session, cookies | Single-use token hash lookup, expiry enforced, client session created, secure HttpOnly cookies | `gallery/tests/test_dual_lane_auth.py` | High |
| `POST /api/galleries/{gallery_id}/guest-access/` | Anonymous guest | anonymous/public access | Gallery/guest session | Guest access session | Published/non-expired gallery required, scoped cookie/token, guest role only | `gallery/tests/test_dual_lane_auth.py` | Medium |
| `POST /api/galleries/{gallery_id}/favorites/` | Gallery client/guest | client/gallery-scoped auth | Gallery/session/photo | Favorite selection | Gallery scope enforced, session DB revalidated, photo must belong to gallery, role visibility enforced | `gallery/tests/test_favorites_engine.py` | High |
| `DELETE /api/galleries/{gallery_id}/favorites/{photo_id}/` | Gallery client/guest | client/gallery-scoped auth | Gallery/session/photo | Favorite selection | Session-scoped delete only, photo/gallery validation | `gallery/tests/test_favorites_engine.py` | High |
| `GET /api/galleries/{gallery_id}/favorites-summary/` | Photographer | photographer dashboard auth | Workspace/Event | Client favorite summary | Photographer JWT required, event scoped to workspace owner | `gallery/tests/test_favorites_engine.py` | High |
| `POST /api/galleries/{gallery_id}/archive/` | Gallery client | client/gallery-scoped auth | Gallery/client session | Full archive job | Client role required, gallery scope enforced, existing job reused when pending/completed | `gallery/tests/test_archive_engine.py`, `gallery/tests/test_download_workflows.py` | High |
| `GET /api/galleries/{gallery_id}/archive/status/` | Gallery client | client/gallery-scoped auth | Gallery/client session/archive job | Archive status and short-lived URL | Client role required, gallery scope enforced, short-lived URL only when completed and not expired | `gallery/tests/test_archive_engine.py` | High |
| `POST /api/galleries/{gallery_id}/archive/favorites/` | Gallery client/guest | client/gallery-scoped auth | Gallery/session/favorites | Favorites archive job | Session revalidated, at least one favorite required, access_session scoped job | `gallery/tests/test_archive_engine.py` | High |
| `GET /api/galleries/{gallery_id}/archive/favorites/status/` | Gallery client/guest | client/gallery-scoped auth | Gallery/session/archive job | Favorites archive status and URL | Session-scoped job lookup, short-lived URL only when completed and not expired | `gallery/tests/test_archive_engine.py` | High |
| `POST /api/v1/ingestion/bulk/` | Photographer | photographer dashboard auth | Workspace/Event/Scene/quota | Heavy Lane manifest, MediaAsset rows, upload tickets | Auth required, scene ownership checked before and inside transaction, quota lock, server-generated object keys, batch limits, duplicate client refs rejected | `ingestion/tests/test_views.py`, `ingestion/tests/test_serializers.py`, `ingestion/tests/test_security.py`, `ingestion/tests/test_quota_ledger.py` | Critical |
| `POST /api/v1/ingestion/webhook/` | Cloudflare R2 | webhook provider access | MediaAsset object key | MediaAsset state | Raw body HMAC, mandatory timestamp, replay window, JSON guard, unknown key ignored, size mismatch quarantine, PENDING-to-READY transition in transaction | `ingestion/tests/test_r2_webhook.py`, `ingestion/tests/test_security.py` | Critical |
| `POST /api/v1/webhooks/cloudflare/r2/` | Cloudflare R2 | webhook provider access | Photo object key | Photo state | HMAC/timestamp verification and idempotent R2 upload completion handling | `webhooks/tests/test_cloudflare.py`, `webhooks/tests/test_r2_webhook.py` | Critical |
| `GET /api/checkout/plans/` | Anonymous / photographer | anonymous/public access | None | Active pricing plans | Active plans only, read-only serializer, no inactive/retired variants | `checkout/tests/test_checkout_security.py`, `checkout/tests/test_models.py` | Medium |
| `POST /api/checkout/generate/` | Photographer | photographer dashboard auth / billing provider access | User subscription | CheckoutSession, Lemon Squeezy checkout request | Auth required, active subscriber blocked, active plan allowlist, redirect allowlist, per-user throttle, cache lock, provider timeout | `checkout/tests/test_checkout_security.py`, `checkout/tests/test_Payment_gateway.py`, `billing/tests/test_transaction_lifecycle.py` | High |
| `POST /api/billing/webhook/` | Lemon Squeezy | billing provider access | User/subscription/workspace | Subscription, audit log, checkout session | Signature required, primary/secondary secret support, empty secret fail-closed, payload hash idempotency, async task handoff | `billing/tests/test_security.py`, `billing/tests/test_transaction_lifecycle.py`, `billing/tests/test_billing_hardening.py` | Critical |
| `POST /api/billing/gallery/upload/` | Photographer | photographer dashboard auth | User subscription quota | Subscription storage ledger | Auth required, image magic-byte check, positive size, row lock quota update | `billing/tests/test_subscription py.py`, `user/tests/billing_test_user.py` | Medium |
| `gallery.tasks.process_fast_lane_asset` | Celery worker | Celery/internal task access | Workspace/Photo/object key | Photo state, R2 object probe, quota refund | UUID normalization, safe key reconstruction, R2 probe bounded by storage client timeout, idempotent status changes | `gallery/tests/test_celery_tasks.py`, `app/tests/test_celery_tasks.py`, `app/tests/test_pipeline_integrity.py` | Critical |
| `gallery.tasks.generate_photo_web_derivative` | Celery worker | Celery/internal task access | Workspace/Photo/object key | R2 original, derivative, watermark | Photo lookup, image-only path, derived server-generated object key, temporary files cleaned | `gallery/tests/test_watermark_engine.py` | High |
| `gallery.tasks.build_gallery_archive` | Celery worker | Celery/internal task access | Gallery/session/archive | R2 originals, archive ZIP, archive job | Only READY assets, visibility filtering, favorites session filter, server-generated archive key, temp file cleanup | `gallery/tests/test_archive_engine.py` | Critical |
| `ingestion.tasks.reap_abandoned_uploads` | Celery worker | Celery/internal task access | Workspace/Photo/quota | Stale pending uploads, quota ledger | R2 existence check, fail-closed on R2 outage, phantom upload quarantine, idempotent refund | `app/tests/test_celery_tasks.py` | High |
| `billing.tasks.process_lemon_squeezy_webhook` | Celery worker | billing provider access / internal task | User/subscription/checkout session | Entitlement state, audit log, processed webhook ledger | Payload hash idempotency, transaction lock, known subscription events only, unpaid ignored, DLQ on failure | `billing/tests/test_transaction_lifecycle.py`, `billing/tests/test_billing_hardening.py` | Critical |
| `core.security` scrubbers and Sentry hooks | Insider/developer mistake | internal telemetry | Logs/telemetry | Emails, IPs, bearer tokens, sensitive fields | Hash/scrub sensitive values, Sentry PII disabled, before_send/breadcrumb hooks | `core/tests/test_security_helpers.py` | High |
| CI scripts and compose gates | Developer/CI | internal build system | Local/CI env | Env placeholders, scans, Docker gates | No secret echo, tracked-file secret scan, env sanity, redacted Bandit | `scripts/ci/*`, local Phase A evidence | High |

## Initial Red-Team Observations

| Area | Observation | Initial Risk | TDD Next Step |
|---|---|---|---|
| Host toolchain | Host Poetry virtualenv points at a stale Python 3.12 path; Docker test image remains usable. | Medium | Record in report; use Docker/controlled host Python fallback for Phase B until Poetry env is repaired. |
| Schema/docs | OpenAPI/docs routes are public. This may be intentional, but schema disclosure should be explicitly reviewed. | Medium | Add a schema exposure test or document intended public docs policy. |
| Client gallery output | Public serializers return user-controlled text fields as JSON. JSON itself is safe, but frontend rendering contract must forbid unsafe HTML rendering. | Medium | Add output-safety tests for scriptable titles/scene names/filenames before deciding whether backend output sanitization is needed. |
| Billing checkout | Checkout generation contains a real HTTP call path to Lemon Squeezy in production code; tests must always mock it. | High | Add/verify tests that Phase B never calls real provider services. |
| Webhook telemetry | Some webhook and task logs include object keys and payload-derived context. Keys are not secrets, but signed URLs/secrets must never appear. | High | Add log redaction tests around signed URL and webhook failures. |

## Phase B Findings And Fixes

| ID | Severity | Area | Threat | Failing Test Added | Patch | Verification |
|---|---|---|---|---|---|---|
| PB-001 | High | Client gallery magic-link auth | A client magic link issued while a gallery was published could still be consumed after the gallery was unpublished, creating a gallery session after access revocation. | `gallery/tests/test_dual_lane_auth.py::DualLaneGalleryAuthTests::test_magic_link_consume_rejects_unpublished_gallery` | `GalleryMagicLinkConsumeView` now revalidates gallery publication and gallery expiry before creating a session, consumes stale links, and returns 403. | Targeted test passed; `gallery/tests/test_dual_lane_auth.py` passed; `unit` passed. |
| PB-002 | High | Billing entitlement custom-data binding | A `subscription_created` webhook with mismatched `custom_data.user_id` and `session_token` could mutate the wrong user's entitlement if the provider sent inconsistent custom data. | `billing/tests/test_transaction_lifecycle.py::FullTransactionLifecycleTests::test_subscription_created_rejects_user_session_mismatch` | Billing task now verifies the checkout session owner matches `custom_data.user_id` before completing checkout or changing subscription state. | Targeted test passed; billing lifecycle tests passed; `unit` passed. |
| PB-003 | High | Logging / telemetry fallback | If DLQ persistence failed, fallback logging emitted raw provider payload content, including email/custom_data fields. | `billing/tests/test_log_redaction.py::BillingWebhookLogRedactionTests::test_dlq_failure_does_not_log_raw_payload_pii_or_tokens` | DLQ fallback logs event ID, DB error type, and payload fingerprint only; raw payload logging removed. | Targeted test passed; redacted Bandit passed; `security` and `unit` passed. |
| PB-004 | Medium | Public gallery output safety | Event slugs generated from photographer-controlled titles could include script delimiters from malicious titles and are exposed in API payloads. | `gallery/tests/test_events_api.py::EventApiTests::test_create_event_slug_is_safe_for_scriptable_title` | Event slug generation now uses Django `slugify` with a safe fallback and preserves the random suffix. | Targeted test passed; `gallery/tests/test_events_api.py` passed; `unit` passed. |

## Phase B Gate Evidence

| Gate | Result | Notes |
|---|---|---|
| secret-hygiene | pass | Host Python fallback; no tracked likely real secrets. |
| env-sanity | pass | Controlled placeholder env values only. |
| redacted Bandit | pass | `bandit issues: 0`. |
| lint | pass | `flake8` returned `0`. |
| targeted gallery auth | pass | 9 passed. |
| targeted gallery events | pass | 6 passed as part of affected suite. |
| targeted billing lifecycle/logging | pass | 17 passed. |
| security | pass | 87 passed. |
| unit | pass | 290 passed. |
| celery | pass | 18 passed. |
| django-smoke | pass | 5 passed after rerun sequentially. |
| integration | pass | 2 passed after rerun sequentially. |
| toxiproxy | pass | 7 passed. |
| docker-build | pass | `docker build .` passed. |

## Remaining Red-Team Work

| Area | Remaining Risk | Recommended Next TDD Target |
|---|---|---|
| Full endpoint authorization matrix | Matrix exists, but every row has not yet received new Phase B adversarial tests beyond existing coverage. | Add endpoint-by-endpoint authorization matrix tests for dashboard vs gallery-token boundaries. |
| Output safety | Slug safety was patched; JSON display fields still rely on frontend escaping contract. | Add explicit client-gallery serialization tests for title, scene, captions, and filenames with scriptable values. |
| Upload and media pipeline | Existing Fast Lane and Heavy Lane tests are strong, but provider-sandbox proof is out of scope here. | Phase C should validate R2/Cloudinary sandbox behavior and object-key policies end to end. |
| Billing state machine | User/session mismatch was fixed; deeper plan downgrade/over-limit policy still needs product-level decisions. | Add entitlement state-machine tests for cancelled, past-due, downgraded, and over-limit workspaces. |
| Webhook replay windows | R2 timestamp replay protection exists; Lemon Squeezy relies on signature and payload-hash idempotency. | Confirm provider timestamp/event metadata support and add bounded replay-window tests if available. |
| Logs and telemetry | DLQ raw payload logging fixed; signed URL and webhook failure logs still need broader redaction assertions. | Add tests for signed URL, webhook signature, token, and email-code absence in logs. |

## Phase B.2 Attack Surface Matrix Update

This update expands the route matrix with object-boundary and missing-test columns before new Phase B.2 patches. The current pass remains limited to red-team security verification; no Darasa domain logic, provider E2E, branch creation, push, or PR is in scope.

| Route/Service | Actor | Auth Domain | Tenant Boundary | Object Boundary | Data Touched | Expected Protection | Existing Tests | Missing Tests | Risk |
|---|---|---|---|---|---|---|---|---|---|
| `GET /api/gallery/fast-lane/photos/{id}/download-url/` | Photographer or scoped gallery client | photographer dashboard JWT/session auth and client/gallery-scoped auth | Workspace and gallery | Photo, scene, event, gallery access session, R2 object key | R2 presigned GET URL | Authorization before URL generation, photographer ownership or gallery token scope/session revalidated, published/non-expired gallery for clients, READY-only asset, short TTL, no signed URL logging | `gallery/tests/test_download_authorization.py`, `gallery/tests/test_download_workflows.py`, `gallery/tests/test_presigned_url_security.py` | Client signed URL after gallery unpublish/expiry; signed URL log redaction | Critical |
| `GET /api/galleries/{gallery_id}/` | Gallery client/guest | client/gallery-scoped auth | Gallery | Gallery, scenes, photos, access session | Client gallery payload | Published and non-expired gallery, gallery token scope, role visibility, READY-only photos, JSON-safe serialization | `gallery/tests/test_dual_lane_auth.py` | Full scriptable field serialization matrix | Critical |
| `POST /api/galleries/{gallery_id}/favorites/` | Gallery client/guest | client/gallery-scoped auth | Gallery | Gallery access session, photo, favorite selection | Favorites | Gallery scope, DB session revalidation, role visibility, photo belongs to gallery, published/non-expired gallery | `gallery/tests/test_favorites_engine.py` | Unpublished/expired gallery favorite mutation regression test | High |
| `POST /api/galleries/{gallery_id}/archive/` | Gallery client | client/gallery-scoped auth | Gallery | Gallery archive job, R2 archive key | Archive job | Client role, gallery scope, published/non-expired gallery, deduplicated pending/completed job, no unauthorized photos | `gallery/tests/test_archive_engine.py`, `gallery/tests/test_download_workflows.py` | Archive spam/resource-abuse bound; unpublished/expired gallery regression test | High |
| `POST /api/checkout/generate/` | Photographer | photographer dashboard auth and billing provider access | User/subscription | Pricing plan, checkout session, redirect URL | Checkout URL | Auth required, active subscriber blocked, active plan allowlist, redirect allowlist, cache lock, provider timeout, no real provider in tests | `checkout/tests/test_checkout_security.py`, `checkout/tests/test_Payment_gateway.py`, `billing/tests/test_transaction_lifecycle.py` | Workspace ownership/entity mapping tests if workspace checkout is introduced | High |
| `POST /api/billing/webhook/` | Lemon Squeezy | billing provider access | User/subscription/checkout session | Provider event, checkout session, subscription, audit ledger | Entitlement state | HMAC verification, empty-secret fail-closed, payload-hash idempotency, user/session custom-data match, DLQ redaction | `billing/tests/test_security.py`, `billing/tests/test_transaction_lifecycle.py`, `billing/tests/test_log_redaction.py` | Provider timestamp/replay-window support verification; out-of-order downgrade matrix | Critical |
| `POST /api/v1/ingestion/bulk/` | Photographer | photographer dashboard auth | Workspace/event/scene | Upload manifest, MediaAsset, object key, quota ledger | Heavy Lane presigned upload tickets | Scene ownership, quota lock, batch limits, server-generated object keys, duplicate client refs rejected, no provider call required | `ingestion/tests/test_views.py`, `ingestion/tests/test_security.py`, `ingestion/tests/test_quota_ledger.py` | Provider-sandbox prefix proof; quota race expansion | Critical |
| `POST /api/v1/ingestion/webhook/` | Cloudflare R2 | webhook provider access | Media asset object key | MediaAsset, R2 object key, upload status | Ingestion state | Raw body HMAC, timestamp/replay protection where supported, unknown keys ignored/quarantined, idempotent mutation | `ingestion/tests/test_r2_webhook.py`, `ingestion/tests/test_security.py` | Duplicate namespace canonical-path proof; log redaction expansion | Critical |
| `gallery.tasks.build_gallery_archive` | Celery worker | Celery/internal task access | Gallery/session | Archive job, favorite selections, R2 originals, archive ZIP | Generated archive | READY-only assets, visibility and favorites session filters, server-generated archive key, temp cleanup, idempotent failure state | `gallery/tests/test_archive_engine.py` | Transaction/failure invariant under R2 partial failure; provider-sandbox proof | Critical |
| CI gate orchestration | Developer/CI | internal build system | Test DB/Redis/Docker services | Test database, Redis keys, Toxiproxy toxics | Gate evidence | No secret echo, deterministic scripts, no concurrent shared DB gates unless isolated, toxic cleanup before/after | `scripts/ci/*`, local Phase A/B evidence | DB namespace isolation for parallel smoke/integration | Medium |

## Phase B.2 Findings And Fixes

| ID | Severity | Area | Threat | Failing Test Added | Patch | Verification |
|---|---|---|---|---|---|---|
| PB-005 | High | Signed URL / client gallery lifecycle | A previously authenticated gallery client could request a fresh R2 signed download URL after the gallery was unpublished or expired because `download_url` revalidated session scope but not gallery lifecycle. | `gallery/tests/test_download_workflows.py::DownloadWorkflowTests::test_client_download_url_rejects_unpublished_gallery_before_presign`; `test_client_download_url_rejects_expired_gallery_before_presign` | `PhotoFastLaneViewSet.download_url` now checks gallery `is_published` and `expires_at` before any presign operation for gallery principals. | New tests failed first with `200 != 403`, then passed; affected gallery signed URL/archive/favorites tests passed; unit passed. |
| PB-006 | Medium | Dashboard/client auth-domain separation | Phase B.2 needed explicit regression proof that client-gallery credentials cannot use dashboard APIs and photographer identities cannot perform client-only mutations. | `gallery/tests/test_auth_domain_boundaries.py` | No code patch required; current auth separation already denies both paths. | 2 auth-domain boundary tests passed; unit passed. |
| PB-007 | High | R2 webhook replay / resource abuse | Duplicate object-created webhooks for already READY assets did not mutate state, but they re-enqueued derivative tasks, allowing retry storms to fan out unnecessary Celery work. | `webhooks/tests/test_cloudflare.py::CloudflareWebhookSecurityTests::test_duplicate_object_created_does_not_enqueue_duplicate_derivative`; `ingestion/tests/test_r2_webhook.py::R2WebhookIdempotencyTests::test_duplicate_ready_webhook_does_not_enqueue_duplicate_derivative` | Both Cloudflare webhook namespaces now return `already_ready` without scheduling duplicate derivative work in the already-READY branch. | New tests failed first with duplicate task calls, then passed; webhooks/ingestion suites passed; Celery and unit gates passed. |

## Phase B.2 Gate Evidence

| Gate | Result | Notes |
|---|---|---|
| secret-hygiene | pass | Host Python fallback; no tracked likely real secrets. |
| env-sanity | pass | Controlled placeholder env values only; no values printed. |
| redacted Bandit | pass | `bandit issues: 0`. |
| lint | pass | Docker `flake8` returned `0`. |
| security | pass | 87 passed. |
| affected gallery | pass | 25 passed. |
| affected ingestion/webhooks | pass | 44 passed. |
| unit | pass | 295 passed. |
| celery | pass | 18 passed. |
| django-smoke | pass | 5 passed using the configured `--ds=app.settings` smoke command. |
| integration | pass | 2 passed. |
| toxiproxy | pass | 7 passed. |
| docker-build | pass | `docker build .` passed. |
