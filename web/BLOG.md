# Publishing Blog Posts (`zycode.dev/blog`)

Blog posts live as plain Markdown files under `web/src/content/blog/`.
You never need to edit Astro/TypeScript web code or run a manual deploy to publish an article: once a `.md` file lands on `main`, Cloudflare Workers Builds automatically rebuilds and publishes `https://zycode.dev` in about one minute.

- English index: `https://zycode.dev/blog/`
- Chinese index: `https://zycode.dev/zh/blog/`
- Article URL: `https://zycode.dev/blog/<slug>/`
- RSS feed: `https://zycode.dev/blog/rss.xml`

Both `/blog/` and `/zh/blog/` list all published posts and let readers filter by language (`All` / `English` / `中文`), so you do **not** have to translate every post.

---

## Two ways to publish

### 1. Local CLI + Git (best for longer posts & local preview)

From `web/`:

```bash
npm run post:new
```

Or pass flags directly:

```bash
npm run post:new -- \
  --title "Architecture notes on ACP permissions" \
  --slug acp-permissions \
  --lang en \
  --desc "How ask, auto, and always-approve modes work." \
  --tags "architecture,security"
```

Then:

1. Edit the generated file `web/src/content/blog/<slug>.md`.
2. Preview locally with `npm run dev` (`http://localhost:4321/blog/`).
3. Commit and push to `main` (or open a PR and merge) — `Web CI` checks types and build, and Cloudflare deploys automatically.

### 2. GitHub Web UI (zero local tools)

1. Open `web/src/content/blog/` on GitHub.
2. Click **Add file → Create new file**, name it `<slug>.md` (use `_template.md` as a reference).
3. Commit directly to `main` (or open a PR). `Web CI` checks the frontmatter and build, and merging to `main` auto-deploys.

---

## Frontmatter reference

```yaml
---
title: "Article title"
description: "Short summary shown in the blog list, RSS feed, and social cards."
date: 2026-11-25
lang: zh            # Required: "zh" or "en"
tags:               # Optional
  - release
  - architecture
updated: 2026-11-26 # Optional: shown alongside publish date and in sitemap lastmod
author: zycode      # Optional (defaults to "zycode")
draft: false        # Optional: true shows in `npm run dev` only, omitted in production
translation: other-slug # Optional: links a bilingual EN/ZH pair together
---
```

### Rules & tips

- **File names**: Use lowercase ASCII letters, digits, and hyphens (e.g. `code-mode-design.md`). The file name without `.md` becomes `<slug>` in `/blog/<slug>/`. Files starting with `_` (such as `_template.md`) are ignored.
- **Bilingual pairs (optional)**: If you publish both an English and Chinese version of the same post, set `translation: <other-slug>` in each file's frontmatter. The header language pill, an inline "Read in English / 阅读中文版" link, `hreflang` tags, and `sitemap.xml` will cross-link them automatically.
- **Images**: Put images under `web/public/blog/<slug>/image.png` and reference them in Markdown as `![Alt text](/blog/<slug>/image.png)`. Keeping images in `web/public/blog/` keeps the strict `img-src 'self' data:` CSP intact with zero external image hosts.
- **Drafts**: Set `draft: true` while writing. Drafts show with a `Draft` badge in `npm run dev` and are excluded from `npm run build`, `sitemap.xml`, and `rss.xml`.
