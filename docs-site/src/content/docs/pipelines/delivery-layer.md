---
title: Delivery Layer
description: How PhotoBox separates gallery delivery from direct downloads.
---

## `delivery_url`

PhotoBox serves browser-facing image delivery through a Cloudinary fetch URL:

```text
https://res.cloudinary.com/{cloud_name}/image/fetch/q_auto,f_webp/{r2_public_url}
```

This keeps Cloudinary in the delivery path while the original binary stays in Cloudflare R2.

### Benefits

- Automatic WebP conversion and quality tuning
- Edge caching close to end users
- No second source of truth for uploaded media

## `download_url`

Direct downloads are generated from R2 presigned GET URLs.

| Property | Behavior |
| --- | --- |
| Issued on demand | URLs are created when needed rather than stored long-term |
| Hard TTL cap | Server code limits the lifetime to 60 seconds |
| Works for large media | Video and original file downloads go straight to R2 |

## Aspect ratio support

PhotoBox stores width and height so the frontend can calculate aspect ratio before the image loads:

```text
aspect_ratio = width / height
```

That lets the gallery layout reserve space ahead of time and avoids cumulative layout shift in masonry views.
