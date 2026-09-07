# Cloudflare R2 lifecycle (manual apply)

PhotoBox cannot set bucket lifecycle from the Django app. Apply this rule in
the Cloudflare dashboard or via Wrangler/Terraform so abandoned multipart
uploads do not linger outside tenant quotas.

Rule source: [`lifecycle-abort-multipart.json`](lifecycle-abort-multipart.json)

- **AbortIncompleteMultipartUpload** after **7 days**
- Keep Redis AOF enabled in compose; `noeviction` fails closed on OOM rather
  than silently dropping PIN/ZIP keys. Leave headroom above `maxmemory` for
  AOF rewrite spikes (compose Redis limit is 768M vs 512mb maxmemory).
