# PhotoBox API

PhotoBox is a **multi-tenant photography SaaS**. Photographers publish client galleries; guests open one share link, enter a PIN, and view photos — no account required.

This repository is the **Django REST API** behind that product. The photographer app and guest gallery UI live in separate frontends and call this API.

## What this API does

- **Galleries as events.** A photographer creates an event, sets a PIN (6+ characters), and gets an unguessable `share_code` such as `/g/k8X2mP9q`.
- **Share like Pixieset, built for WhatsApp.** Guests prove the PIN; email is optional. Rotating the PIN invalidates existing guest sessions.
- **Two upload lanes, one vault.** Small images go Fast Lane (`202` then Celery). RAW, video, and bulk go Heavy Lane as presigned POSTs straight to **Cloudflare R2**. R2 is the only copy of the bytes.
- **Cheap, sharp viewing.** Gallery tiles are sized, signed Cloudinary fetches of the web derivative — never the original. Downloads are short-lived R2 URLs (60s).
- **Tenant isolation.** Every photographer query is scoped through their workspace. Public routes resolve by `share_code`, not sequential IDs.
- **Billing.** Lemon Squeezy for card checkout; Daraja/M-Pesa callback stub for Kenya KES.

Architecture, security, and operations docs: [`photobox-docs-site/`](photobox-docs-site/). Live OpenAPI is at `/api/schema/` and **requires a photographer JWT**.

## Stack

| Layer | Choice |
| --- | --- |
| API | Python 3.12, Django 5.2 LTS, Django REST Framework |
| Jobs | Celery + Redis |
| Data | PostgreSQL |
| Vault | Cloudflare R2 |
| Tiles | Cloudinary fetch (no SDK uploads) |
| Run | Docker Compose |

## Local development

```bash
git clone https://github.com/ALEX-MUTHOMI/PhotoBox-API.git
cd PhotoBox-API
cp .env.example .env
docker compose up -d db redis
docker compose run --rm app python manage.py migrate
docker compose up app celery
```

The API listens on `http://localhost:8001` by default (`APP_PORT`). Liveness: `GET /health/`. Readiness: `GET /api/health-check/`.

## Tests

```bash
docker compose run --rm test unit
docker compose run --rm test kenya
```

`kenya` covers share codes, PIN-only guests, masonry delivery, photographer-day contracts, Daraja isolation, and the JSON-only API surface.

## What this repo is not

It is not the gallery website, the photographer dashboard, or a print catalog. Those clients consume this API.
