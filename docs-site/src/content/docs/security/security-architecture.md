---
title: Security Architecture
description: Layered controls across transport, application logic, webhook ingress, billing integrity, identity, and retention.
---

PhotoBox applies security as layered control planes rather than a single gate.

## Layer 1: transport and perimeter

| Defense | Implementation |
| --- | --- |
| Nginx upload cap | `client_max_body_size 5m` rejects oversized bodies early |
| Django upload memory cap | `DATA_UPLOAD_MAX_MEMORY_SIZE = 5MB` provides a second gate |
| Strict CORS allowlist | `CORS_ALLOW_ALL_ORIGINS = False` |
| Rate limiting | Fast Lane, Heavy Lane, and anonymous traffic have separate thresholds |
| JWT auth | Short-lived access tokens and rotating refresh tokens |
| Password hashing | Argon2 first, PBKDF2 fallback |

## Layer 2: application logic

| Defense | Implementation |
| --- | --- |
| Tenant isolation | Queries resolve through scene, event, workspace, and user ownership |
| Cross-tenant shield | Write paths verify scene ownership before creating assets |
| Magic byte validation | Pillow integrity and format checks for accepted image types |
| Decompression bomb limit | Pixel ceilings prevent malicious image expansion |
| Quota gate | Atomic updates prevent concurrent overspend |
| Quota refund | Deletes and failed uploads reconcile storage usage |

## Layer 3: machine-to-machine webhook security

| Defense | Implementation |
| --- | --- |
| HMAC verification | Timing-safe digest comparison |
| Replay window | Freshness enforcement for signed ingestion events |
| Ghost key tolerance | Unknown object keys return `200` to stop retry storms |
| Quarantine path | Declared versus actual size mismatches are isolated |
| Payload size guard | Oversized webhook bodies are rejected early |
| Secret separation | Webhook signing secrets are separate from storage credentials |

## Billing integrity

- Checkout upgrade checks read durable subscription state, not transient request attributes.
- Idempotency keys derive from raw payload hashes instead of unauthenticated event headers.
- Cancellation does not destroy the provider subscription identifier needed for delayed reconciliation.

## Identity hardening

- Google social sign-in rejects identities without verified email claims.
- Existing local accounts are not silently linked to new social identities unless the provider UID already matches.
- Auth telemetry logs hashed principals and hashed IP fingerprints instead of raw email addresses or source IPs.

## Retention and data lifecycle

| Tier | Retention | Enforcement |
| --- | --- | --- |
| Free | 30 days | Nightly Celery purge |
| Pro | 365 days | Nightly Celery purge |
| Enterprise | Unlimited | Manual purge only |
| Soft-delete grace | 30 additional days | Hard delete from R2 after grace expires |
