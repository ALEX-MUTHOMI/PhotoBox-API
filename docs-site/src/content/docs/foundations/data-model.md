---
title: Data Model
description: The event, scene, and photo hierarchy plus the asset lifecycle used by PhotoBox.
---

## Entity hierarchy

```text
Workspace
  -> Event
     -> Scene
        -> Photo / MediaAsset
```

### Workspace

The workspace is the tenant boundary. Every gallery object must resolve back to a single owning workspace.

### Event

An event represents the client-facing gallery container, for example a wedding or branded shoot. It carries publication state and client notification targets.

### Scene

A scene is the presentation unit inside an event, such as `Ceremony` or `Reception`.

### Photo / MediaAsset

The leaf asset stores vault location, processing state, dimensions, and delivery metadata.

## Asset fields that matter most operationally

| Field | Meaning |
| --- | --- |
| `r2_object_key` | Canonical vault path in Cloudflare R2 |
| `status` | Lifecycle state for asynchronous processing |
| `is_processed` | Indicates that the upload completion path has succeeded |
| `width`, `height` | Dimensions used for gallery rendering |
| `delivery_url` | Cloudinary fetch URL for browser viewing |
| `download_url` | Short-lived R2 presigned URL for direct download |

## Status state machine

```text
PENDING
  |- READY
  |- FAILED
  |- QUARANTINED
  `- EXPIRED
```

| State | Meaning |
| --- | --- |
| `PENDING` | Metadata exists, but storage confirmation is not complete yet |
| `READY` | Upload and post-processing completed successfully |
| `FAILED` | Retries exhausted and the asset cannot be delivered |
| `QUARANTINED` | Upload was received but failed integrity or size expectations |
| `EXPIRED` | Retention or gallery TTL policy has aged the asset out |

## MediaAsset alias

The ingestion layer imports `MediaAsset` from `gallery.models`, where it aliases the `Photo` model. This keeps ingestion code semantically clear without introducing a second table or migration path.

## Model invariants

- Every asset must be reachable from a tenant-owned workspace.
- Asset status changes must be idempotent because workers and webhooks can race.
- Delivery metadata is derived from the vault key rather than treated as the storage source of truth.
