# PhotoBox Docs

Production-grade Starlight documentation portal for the PhotoBox platform.

## Commands

All commands run from `docs-site/`:

| Command | Purpose |
| --- | --- |
| `npm install` | Install the local docs toolchain |
| `npm run dev` | Start the local docs server |
| `npm run check` | Run Astro type and content checks |
| `npm run build` | Produce a static production build |
| `npm run validate` | Run the full docs verification pass (`check` + `build`) |
| `npm run preview` | Preview the generated build locally |

## Deployment Notes

- This site is static and can be deployed safely to Cloudflare Pages, Netlify, Vercel, S3 + CDN, or Nginx.
- The canonical site URL is read from `DOCS_SITE_URL` and falls back to `https://docs.photobox.app`.
- Search is powered locally by Pagefind and does not require a hosted third-party search service.
- The site avoids remote font CDNs and uses local-first font stacks for predictable static hosting.
- Astro 6 requires Node.js `>=22.12.0`.

## Content Layout

```text
src/content/docs/
  foundations/
  pipelines/
  security/
  operations/
```

The long-form architecture markdown from the main repository is broken into these sections so the docs portal is easier to navigate, search, and maintain.
