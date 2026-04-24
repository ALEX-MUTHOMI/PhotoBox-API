---
title: API Reference
description: High-level endpoint map for gallery operations, ingestion, and core system routes.
---

Interactive schema and ReDoc remain available from the Django application at `/api/schema/` and `/api/docs/`. This page is the high-level route map that engineers can scan quickly.

## Gallery endpoints

| Method | Endpoint | Description | Auth |
| --- | --- | --- | --- |
| `GET` | `/api/gallery/events/` | List the current photographer's events | JWT |
| `POST` | `/api/gallery/events/` | Create a new event | JWT |
| `PATCH` | `/api/gallery/events/{id}/` | Update an event, including publish transitions | JWT |
| `DELETE` | `/api/gallery/events/{id}/` | Delete an event | JWT |
| `GET` | `/api/gallery/scenes/` | List scenes, optionally filtered by event | JWT |
| `POST` | `/api/gallery/scenes/` | Create a scene | JWT |
| `POST` | `/api/gallery/fast-lane/photos/` | Upload a Fast Lane photo and receive `202 Accepted` | JWT |
| `GET` | `/api/gallery/fast-lane/photos/` | List photos, optionally filtered by scene | JWT |
| `DELETE` | `/api/gallery/fast-lane/photos/{id}/` | Delete a photo and refund quota | JWT |

## Ingestion endpoints

| Method | Endpoint | Description | Auth |
| --- | --- | --- | --- |
| `POST` | `/api/v1/ingestion/bulk/` | Submit a bulk upload manifest | JWT |
| `POST` | `/api/v1/ingestion/webhook/` | Receive upload completion signals from R2 | HMAC |

## System endpoints

| Method | Endpoint | Description | Auth |
| --- | --- | --- | --- |
| `GET` | `/api/health-check/` | Health probe for uptime systems | None |
| `GET` | `/api/schema/` | OpenAPI schema | None |
| `GET` | `/api/docs/` | ReDoc documentation | None |
| `POST` | `/api/user/create/` | Register a new account | None |
| `POST` | `/api/user/token/` | Obtain a JWT pair | None |
