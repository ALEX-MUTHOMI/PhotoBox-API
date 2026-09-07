# PhotoBox scale envelopes and elastic provisioning

`dsa_load.py` steps concurrent authenticated gallery GETs. Photographer/guest
counts below are **floors**, not ceilings — traffic can exceed them tonight.
Elastic compute + load-shed keep latency \(W\) flat; a human must not be the
autoscaler at 3am.

## Load harness rules

- Reuse **one photographer JWT** and **pre-minted guest sessions** — never stampede SMTP / magic-link consume.
- **Do not** disable throttles for the run; venue NAT must surface as `http_429`.
- **P99** is computed only on **accepted** responses (`status < 400`).
- **429s** are counted in `http_429` separately from latency.

```bash
python scripts/scale/dsa_load.py \
  --base-url http://127.0.0.1:8000 \
  --gallery-id 00000000-0000-0000-0000-000000000001 \
  --cookie-file scripts/scale/cookies.example.txt \
  --levels 100,500,1000
```

Prefer share-code paths in staging: `GET /api/galleries/g/<share_code>/`.

Cookie file: one `Cookie` header value per line (`gallery_access=...; gallery_session=...`).

Set `PHOTBOX_SCALE_ENVELOPE=1|2|3` for fortress load shape (see below).

---

## Governing math (why W must stay flat)

**Little’s Law:** \(L = \lambda \cdot W\). If PIN hashing or a missing index pushes \(W\) from 10ms to 500ms, the **same wedding traffic** needs ~50× Postgres/Redis connections. Autoscale of web pods alone multiplies \(L\) until PgBouncer dies. **Invariant: keep \(W\) flat; autoscale only after \(W\) is bounded.**

**Universal Scalability Law:** throughput is limited by serialization \(\sigma\) (quota `SELECT FOR UPDATE`, ZIP Lua, gallery PIN global keys) and crosstalk \(\kappa\) (Redis gossip, Celery broadcast, chatty cache invalidation). Do **not** add a global “active galleries” counter on every GET. Do **not** autoscale on a metric an attacker can increment (unauthenticated ZIP enqueue, Heavy Lane ticket flood).

---

## Envelope floors (architecture unlocks)

| Envelope | Base quota (floor) | Must already have | Fortress load |
| --- | --- | --- | --- |
| **1** | ~1k photographers / 100k guests | share_code unique btree; PIN Redis-before-hash + fail-closed; ZIP leased fail-closed; keyset pagination; signed tiles; JSON-only public API | `100,500` on GH-hosted |
| **2** | ~10k / 1M | PgBouncer sized vs workers; Redis Cluster (or gallery-slotted keys); Celery HPA **per bulkhead**; EXPLAIN at larger N; Turnstile after PIN floods | `500,1000` (staging or self-hosted if wall time >30m) |
| **3** | ~100k / 2M | photo partition / optional workspace cells; CF WAF; PIN checks on **primary only**; staging-cell soak | skip GH-hosted; require staging |

`PHOTBOX_SCALE_ENVELOPE` is the **architecture floor**. Replica count is HPA output, not this variable. Promo CI never waits for envelope 3.

---

## Elastic provisioning (no 3am scale-out)

Deploy today may be Compose (`docker-compose-deploy.yml`). Elastic scale needs an orchestrator (ECS / Cloud Run / Kubernetes + KEDA). Example ScaledObjects: [`deploy/keda/scaledobjects.example.yaml`](../../deploy/keda/scaledobjects.example.yaml). Until then: **load-shed + queues**, not “restart with more RAM.”

| Layer | Autoscale? | Signal | Hard stop |
| --- | --- | --- | --- |
| App replicas | Yes (HPA/KEDA) | p99 latency, in-flight \(L\) — **not CPU alone** | `max_replicas × worker_concurrency < PgBouncer default_pool_size` |
| Celery `image-processing` | Yes, own HPA | queue depth | per-tenant + global concurrency; 429/retry on broker full |
| Celery `archive-zip` | Yes, **separate** HPA | archive-zip depth | never scale zip workers past RAM-bound ZIP leases |
| Postgres | **No** pod HPA | replica lag, CPU, bloat | shed 503 or add replica via operator — never 20 primaries |
| Redis | planned shard add | evictions, latency | PIN fail-closed **429** if a slot is down |
| PgBouncer | size from formula | `cl_waiting` | if `cl_waiting` sticks: **503 API**; do **not** add Django replicas |

**Migrate once:** HPA replicas must **not** run `migrate` / `makemigrations` (lock storm). Use a one-shot migrate job. Liveness probes must be cheap (not full gallery SELECTs).

**Immutable digest on scale-out:** pull `ghcr.io/.../photobox-api-app@sha256:...` (or `:ci-<sha>` / `:<sha>` from promo). Never autoscale from `:latest`.

### PgBouncer sizing sketch

```text
max_app_replicas * gunicorn_workers * threads  <=  pool_size * (1 - headroom)
```

If `cl_waiting > 0` for N seconds: shed anonymous share_code probes first, then guest GET; never fail-open PIN.

---

## Attacker-proof autoscaling (abuse SLO)

- Fill Heavy Lane / ZIP queues → naive Celery HPA → bill bomb. **Cap:** per-gallery ZIP lease = 1, global ZIP leases RAM-bound, HPA `maxReplicas` ceiling.
- Hammer `/api/health-check/` if it opens DB+Redis every probe → pool stampede. **Fix:** shallow liveness; readiness separate; CF rate-limit health.
- PIN botnet × hasher → CPU; scaling web pods **makes it worse**. **Fix:** Redis cheap counter before hash; shed guest-access 429; do not HPA on CPU during PIN floods.
- Metric cardinality: labels like `gallery_id` / `share_code` → millions of series. **Fix:** aggregate SLO; drop tenant labels (Security Insights lesson).
- `PHOTBOX_DAST` / `DEBUG` must never appear on autoscaled prod task defs.

### Alert on error budget, not CPU

Page on: p99, `cl_waiting`, ZIP lease exhaustion, Redis fail-closed 429 rate, error budget burn. Autoscale should already have reacted; pages mean \(W\) is no longer flat or a bulkhead cap was hit.

### Edge caching truth

Do **not** CDN-cache `/api/galleries/` (PIN / `Vary: Cookie` / `no-store`). Edge absorbs **tiles** (Cloudinary/R2 CDN) and static docs — not auth-varying gallery JSON. Cached masonry without PIN is a product-ending bug.

---

## Production notes (Phase E)

- Celery workers must consume the **`archive-zip`** queue for ZIP builds (separate from image-processing). Deploy compose runs a dedicated `celery-archive` service with `--prefetch-multiplier=1` and `stop_grace_period: 10m`.
- Redis deploy policy is **`noeviction`** (keep AOF). Do not use `allkeys-lru` on the shared PIN/ZIP/broker instance. Size `maxmemory` with AOF rewrite headroom.
- Apply R2 `AbortIncompleteMultipartUpload` lifecycle (`deploy/r2/`).
- PgBouncer: app DB user **least-privilege** (DML only — no CREATEDB / no SUPERUSER).
- `CONN_MAX_AGE=0` + `DISABLE_SERVER_SIDE_CURSORS` stay required behind transaction pooling. Runtime `statement_timeout=5s` is skipped for `migrate` / `RUN_MIGRATIONS=1`.
- ZIP lease **fail-closed** when `DEBUG=False` and Redis is unavailable.
- CORS: exact photographer/client origins only — never `*.vercel.app` / wildcard previews with credentials.
- Enable `TRUST_CLOUDFLARE_CLIENT_IP` only behind Cloudflare that strips client-supplied CF-Connecting-IP.
- Idempotency: LS/R2/Daraja webhooks and guest-access session reuse already; archive-zip **enqueue** must stay single-active-job per gallery under retries.
- KEDA examples are templates; HTTP p99 Prometheus metrics are **not** emitted yet — use Redis queue depth for workers.
