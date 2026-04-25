---
title: Webhook System
description: Completion signals, idempotency, and webhook-specific hardening paths.
---

## Ingestion webhook

**Endpoint:** `POST /api/v1/ingestion/webhook/`  
**Auth:** HMAC-SHA256  
**URL name:** `r2-ingestion-webhook`

### Security pipeline

1. Reject payloads above the size guard.
2. Capture the raw body before any parser mutates it.
3. Extract required signature headers.
4. Verify the HMAC with `hmac.compare_digest()`.
5. Enforce the freshness window to limit replay attempts.
6. Parse JSON with explicit error handling.
7. Ignore actions that are not `PutObject`.
8. Tolerate unknown keys with `200 OK` so providers stop retrying.
9. Quarantine size mismatches.
10. Skip assets that are already `READY`.
11. Perform the final state change atomically.

## Legacy webhook

**Endpoint:** `POST /api/v1/webhooks/cloudflare/r2/`  
**URL name:** `r2-webhook-ingress`

This endpoint follows the same verification posture but supports older integration paths that transition assets to `UPLOADED`.

## Operational principles

- Webhooks are authoritative for Heavy Lane completion.
- Payload hashes or object keys must be safe to process more than once.
- Unknown or delayed provider traffic should not destabilize the API.
