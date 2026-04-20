---
title: Architecture Overview
description: The core system model for PhotoBox and the unified vault pattern that shapes ingestion and delivery.
---

PhotoBox is a multi-tenant photography SaaS platform built on Django 4.x with an event-driven architecture. The platform serves photographers who upload, curate, and deliver client galleries without letting media handling complexity leak into the user experience.

## Unified vault pattern

```text
Photographer dashboard
  |-- Fast Lane: small image uploads
  |-- Heavy Lane: raw, video, and bulk upload manifests
          |
          v
Cloudflare R2 vault
  |-- Tenant-isolated object keys
  |-- Single binary source of truth
          |
          |-- Cloudinary fetch delivery for optimized gallery viewing
          |-- R2 presigned GET URLs for direct downloads
```

## Why the architecture is shaped this way

| Principle | Implementation |
| --- | --- |
| Single source of truth | Cloudflare R2 stores every binary. Cloudinary is only a fetch and transform layer. |
| Async-first execution | Slow storage work and reconciliation happen out of band through Celery and webhook loops. |
| Tenant isolation | Ownership checks flow through `workspace__user=request.user` style query chains. |
| Defense in depth | Nginx, Django upload limits, Pillow validation, quota gates, and asynchronous recovery form a layered boundary. |

## Two lanes, one control plane

PhotoBox separates uploads by operational shape instead of trying to make one endpoint do everything:

- Fast Lane accepts smaller browser-friendly images and returns quickly with a `202 Accepted`.
- Heavy Lane signs direct-to-R2 uploads for large assets so Django stays out of the data path.
- Both lanes converge on the same vault, data model, and delivery primitives.

## Architectural outcomes

- Small uploads feel responsive because the API accepts the request and hands off the expensive storage work.
- Large uploads are safer because the browser talks directly to R2 instead of pushing heavy media through the Django container.
- Gallery viewing stays fast because delivery and download concerns are separated.
- Operational recovery is easier because asset state moves through explicit statuses rather than implicit filesystem assumptions.
