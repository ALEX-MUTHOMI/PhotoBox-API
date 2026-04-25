
# 📸 PhotoBox API

![Python](https://img.shields.io/badge/Python-3.12-blue?style=for-the-badge&logo=python)
![Django](https://img.shields.io/badge/Django-4.x-092E20?style=for-the-badge&logo=django)
![Celery](https://img.shields.io/badge/Celery-Async-37814A?style=for-the-badge&logo=celery)
![Cloudflare R2](https://img.shields.io/badge/Cloudflare_R2-Vault-F38020?style=for-the-badge&logo=cloudflare)
![Security](https://img.shields.io/badge/Security-Hardened-red?style=for-the-badge)

**PhotoBox** is an enterprise-grade, multi-tenant photography SaaS platform built on an **Event-Driven Architecture (EDA)**. It provides professional photographers with a highly resilient, asynchronous backend to upload, curate, and deliver client galleries at massive scale.

## 🏗 Architecture & The Unified Vault Pattern

PhotoBox implements a **Unified Vault Pattern**, utilizing Cloudflare R2 as the absolute single source of truth for all binary assets. Django orchestrates state, quotas, and security, but stays out of the data path for heavy uploads.

* **Fast Lane (≤ 5MB):** Synchronous validation, atomic quota reservation, returns `202 Accepted`, hands off to Celery for R2 upload.
* **Heavy Lane (> 5MB / Bulk):** Generates presigned POST tickets for direct-to-R2 uploads. Zero network bottleneck on the Django application servers.
* **Delivery:** Cloudinary acts strictly as a Fetch Proxy (WebP conversion + Edge Cache) reading directly from the R2 origin. No SDK uploads. No data duplication.
* **Secure Downloads:** Time-limited (60s) presigned R2 GET URLs generated on demand.

## 🚀 Key Features

* **Multi-Tenant Isolation:** Deep QuerySet filtering (`scene__event__workspace__user`) guarantees absolute cryptographic separation of photographer assets.
* **Ruthless Security Posture:** Built-in defenses against Decompression Bombs (Zip bombs), MIME-type spoofing, Server-Side Request Forgery (SSRF), and Cross-Tenant IDORs.
* **Atomic Economic Ledger:** Storage quotas are strictly enforced using database-level row locks (`SELECT FOR UPDATE`) to prevent concurrent TOCTOU (Time-of-Check to Time-of-Use) race conditions.
* **Idempotent Webhooks:** Cloudflare R2 and Lemon Squeezy billing webhooks are secured via HMAC-SHA256, strictly validated against replay attacks, and processed idempotently via payload hashing.

## 🛠 Tech Stack

* **Core:** Python 3.12, Django 4.x, Django REST Framework (DRF)
* **Database:** PostgreSQL (with `django-db-locks` for atomic operations)
* **Async Workers:** Celery + Redis
* **Storage:** Cloudflare R2 (S3-compatible Unified Vault)
* **CDN / Image Optimization:** Cloudinary
* **Billing:** Lemon Squeezy
* **Infrastructure:** Docker, Docker Compose, Nginx

## 💻 Getting Started (Local Development)

### 1. Prerequisites
* Docker & Docker Compose
* Git

### 2. Environment Setup
Clone the repository and set up your `.env` file (see `.env.example` for required keys):

```bash
git clone https://github.com/your-org/photobox-api.git
cd photobox-api
# Create and populate your .env file

