---
title: Upload Pipelines
description: Fast Lane and Heavy Lane flows, including quota control and event-driven completion semantics.
---

PhotoBox uses two upload lanes because small images and large media have very different operational requirements.

## Fast Lane

**Endpoint:** `POST /api/gallery/fast-lane/photos/`  
**Auth:** JWT bearer token  
**Content type:** `multipart/form-data`  
**Throttle:** `30 uploads/minute` per user

### Request flow

1. Reject files above the Fast Lane size limit before expensive work starts.
2. Verify the target scene belongs to the authenticated workspace.
3. Validate the binary using Pillow integrity checks, dimensions checks, and an allowlist of safe formats.
4. Enforce the workspace quota before the row is committed.
5. Create a `Photo(status='PENDING', is_processed=False)` record inside the database transaction.
6. Dispatch `process_fast_lane_asset.delay(photo_id)` and return `202 Accepted`.

### Worker flow

`process_fast_lane_asset` later:

1. Reads the locally staged upload from Django storage.
2. Streams the file to R2 with `boto3.upload_fileobj()`.
3. Writes the canonical `r2_object_key`.
4. Marks the asset `READY` and `is_processed=True`.
5. On permanent failure, marks `FAILED` and atomically refunds storage quota.

## Heavy Lane

**Endpoint:** `POST /api/v1/ingestion/bulk/`  
**Auth:** JWT bearer token  
**Content type:** `application/json`  
**Throttle:** `10 manifests/minute` per user

### Request flow

1. Validate filenames and strip dangerous or malformed input.
2. Lock the workspace quota row with `SELECT FOR UPDATE`.
3. Generate presigned POST tickets with strict size constraints.
4. Insert all `MediaAsset` rows with `bulk_create()`.
5. Return upload tickets so the client can send binaries directly to R2.

### Why Heavy Lane bypasses Django data transfer

- Django avoids CPU and memory pressure from raw and video uploads.
- Browser retries happen against object storage instead of the app container.
- The API remains a control plane rather than a transfer bottleneck.

## Event-driven control loops

PhotoBox uses three separate asynchronous loops with different failure semantics:

### Fast Lane acceptance loop

The request reserves quota, records a `PENDING` asset, and schedules the vault upload. Celery workers reconcile or refund later.

### Heavy Lane completion loop

The request signs tickets and inserts rows, but upload completion is authoritative only when the R2 webhook arrives and transitions `PENDING -> READY` or `QUARANTINED`.

### Billing reconciliation loop

Webhook consumers verify provider signatures, derive an idempotency key from the raw payload, and process the resulting state change asynchronously.
