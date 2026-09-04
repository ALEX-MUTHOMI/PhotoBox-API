# DSA scale load harness

`dsa_load.py` steps concurrent authenticated gallery GETs at **100 / 500 / 1000**.

## Rules (from the scale-security plan)

- Reuse **one photographer JWT** and **pre-minted guest sessions** — never stampede SMTP / magic-link consume.
- **Do not** disable throttles for the run; venue NAT must surface as `http_429`.
- **P99** is computed only on **accepted** responses (`status < 400`).
- **429s** are counted in `http_429` separately from latency.

## Example

```bash
python scripts/scale/dsa_load.py \
  --base-url http://127.0.0.1:8000 \
  --gallery-id 00000000-0000-0000-0000-000000000001 \
  --cookie-file scripts/scale/cookies.example.txt \
  --levels 100,500,1000
```

Use share-code public paths in staging:

```text
GET /api/galleries/g/<share_code>/
```

Cookie file format: one `Cookie` header value per line:

```
gallery_access=...; gallery_session=...
```

## Production notes (Phase E)

- Run Celery workers consuming the **`archive-zip`** queue for ZIP builds.
- PgBouncer: app DB user must be **least-privilege** (DML only — **no CREATEDB / no SUPERUSER**).
- `CONN_MAX_AGE=0` + `DISABLE_SERVER_SIDE_CURSORS` stay required behind transaction pooling.
- ZIP lease **fail-closed** when `DEBUG=False` and Redis is unavailable.
- CORS: exact photographer/client origins only — never `*.vercel.app` / wildcard previews with credentials.
- Enable `TRUST_CLOUDFLARE_CLIENT_IP` only behind Cloudflare that strips client-supplied CF-Connecting-IP.
