# zycode web

This folder holds the product site for zycode. The site is a static Astro project.
Cloudflare serves the built files from its edge network.

The site has two languages: English at `/` and Chinese at `/zh/`.

> **Note:** This site is not part of the Python packages. The root Ruff and pytest
> jobs do not read this folder.

## Requirements

- Node.js 22.23.4 or later. The file `.node-version` names version 24.
- npm 10 or later.

The file `.npmrc` sets `engine-strict`. `npm install` stops on a Node.js version that is too old.

The field `packageManager` in `package.json` names npm 11.19.1. Cloudflare Workers Builds
and the web CI install this version, so a lockfile installs the same way in both. To use
another npm version, change only this field.

## Commands

Run all commands in this folder (`web/`).

| Command              | What it does                                                     |
| -------------------- | ---------------------------------------------------------------- |
| `npm install`        | Installs the packages.                                           |
| `npm run dev`        | Starts the development server at `http://localhost:4321`.        |
| `npm run check`      | Checks the types in the `.astro` and `.ts` files.                |
| `npm run build`      | Builds the site into `dist/`.                                    |
| `npm run preview`    | Serves `dist/` on your computer.                                 |
| `npm run deploy:dry` | Builds the site and shows what `wrangler` would upload.          |
| `npm run deploy`     | Builds the site and uploads it to Cloudflare.                    |

> **Caution:** Use the development server and the preview server only to look at the
> site on your own computer. Do not use a development computer to host the site.
> The site is static files. Cloudflare serves them, so no server of your own is necessary.

## Folder map

```
web/
├── brand/             Pictures that the site does not serve: the avatar and the header for X
├── public/            Files that are copied without a change: favicon, social card, _headers
├── src/
│   ├── components/    One file for each part of the page
│   ├── data/site.ts   The links and the list of packages
│   ├── i18n/          The text of the page: en.ts, zh.ts, types.ts
│   ├── layouts/       Base.astro: the <head> tags and the page frame
│   ├── lib/           highlight.ts (code colors, made at build time), preview.ts
│   ├── pages/         index.astro, zh/index.astro, 404.astro, robots.txt.ts, sitemap.xml.ts,
│   │                  dev/ (the sources of pictures; these pages exist only in `npm run dev`)
│   ├── scripts/       main.ts: the theme switch and the reveal effect
│   └── styles/        global.css: colors, type, and layout rules for the whole site
├── astro.config.mjs   Astro settings: site address, languages, fonts
└── wrangler.jsonc     Cloudflare Workers settings
```

## Change the text

All text is in `src/i18n/en.ts` and `src/i18n/zh.ts`. The type `Messages` in
`src/i18n/types.ts` gives both files the same keys. If you add a key to one file,
`npm run check` shows an error until you add it to the other file.

Obey these rules for the content:

- Every claim must have a source in this repository. Do not add a claim that you cannot show.
- The status table must be equal to the table "Status" in the root `README.md`.
  The package list is in `src/data/site.ts`.
- The numbers in the section "Evidence" must be equal to
  `docs/benchmarks/FRONTIER-HARNESS-30-EVAL-REPORT.md`. Keep the notes about the limits of the test.
- The terminal demo in the first screen is an illustration. Its caption says so. Keep the caption.
- Do not say that zycode is part of the pi project or that the pi project approves it.

## Colors and themes

The colors are CSS variables at the top of `src/styles/global.css`. The variables use
`light-dark()`, so the theme follows the system setting of the visitor. The toggle in the
header saves the choice of the visitor in `localStorage` under the key `zycode-theme`.

The colors meet WCAG 2.2 level AA: a ratio of 4.5:1 for text, and 3:1 for large text and
interface parts. Check this again when you change a color.

## Social card

The file `public/og.png` is the image for links that people share. Its size is 1200 × 630 pixels.

The source of the image is the page `/dev/og/`. This page exists only in `npm run dev`.
The build does not make it. To make the image again:

1. Run `npm run dev`.
2. Open `http://localhost:4321/dev/og/` in a window that is 1200 pixels wide.
3. Save a screenshot of the card, 1200 × 630 pixels, as `public/og.png`.

## Pictures for X

The folder `brand/` holds the pictures of the X account. The site does not serve them.

| File                  | Size        | Source                                                        |
| --------------------- | ----------- | ------------------------------------------------------------- |
| `zycode-avatar.png`   | 1024 × 1024 | `zycode-avatar.svg`. It has the mark of `public/favicon.svg`. |
| `zycode-x-header.png` | 1500 × 500  | The page `/dev/x/header/`. It exists only in `npm run dev`.   |

X cuts the avatar to a circle. The mark stays well inside the circle.

To make the header again, do the steps of the section "Social card", with these changes:
open `http://localhost:4321/dev/x/header/` in a window that is 1500 pixels wide,
and save a screenshot of the picture, 1500 × 500 pixels, as `brand/zycode-x-header.png`.

The avatar of X covers the lower left of the header. The apps put a back button on the upper left.
Do not put text in these two areas.

## Security headers

The file `public/_headers` sets the security headers and the cache rule. The
`Content-Security-Policy` allows only files from the same origin.

If you add a script, a font, an image, or a request from another origin, add that origin
to the policy. If you do not, the browser blocks it.

## Deploy

The site is a Cloudflare Worker that has static assets only. It has no script.
Cloudflare serves the files in `dist/` directly. The address of the site is
`https://zycode.dev`.

There are two ways to deploy. A merge into `main` deploys the site by itself (see
"Deploy after a merge"). You can also deploy from your computer (see "Deploy by hand").

### Deploy after a merge

Cloudflare Workers Builds deploys the site. It runs on the machines of Cloudflare, not on
GitHub Actions.

1. A pull request that changes a file in `web/` is merged into `main`.
2. Cloudflare clones the repository and uses `/web` as the root directory.
3. Cloudflare runs the build command `npm run check && npm run build`. Then it runs the
   deploy command `npx wrangler deploy`.
4. The new files are live on `https://zycode.dev`. A run takes about one minute.

A merge that changes no file in `web/` starts no build. A change to the Python packages
is an example.

GitHub shows the result on the merge commit as the check `Workers Builds: zycode-web`.
To read the log, open the Worker `zycode-web` in the Cloudflare dashboard. Open
**Deployments**, then **View build history**. Cloudflare keeps each log for 90 days.

> **Caution:** A merge into `main` that changes `web/` changes the live site. Read the
> pull request before you merge it.

The workflow `.github/workflows/web-ci.yml` runs `npm run check` and `npm run build` on
each pull request that changes `web/`, and on each push to `main` that changes `web/`.
Its check is `Site (check + build)`. It does not deploy. It shows an error before the
merge.

### Deploy by hand

Use this way to deploy from your computer. It deploys to the same Worker and the same
address.

1. Run `npx wrangler login`. A browser window opens. Select **Allow**. You do this one
   time on each computer.
2. Run `npm run deploy:dry`. Make sure that the file list is correct.
3. Run `npm run deploy`. Wrangler uploads the files and keeps the Custom Domain
   `zycode.dev` on the Worker.
4. Open `https://zycode.dev/` and `https://zycode.dev/zh/`. Make sure that both pages load.

> **Caution:** Do not put an API token in a file in this repository or in a command line.
> Use `wrangler login`. If a job without a person must deploy, put the token in an
> environment variable of that job.

> **Caution:** `wrangler.jsonc` binds `zycode.dev` to this Worker. Do not run
> `wrangler deploy` with `--name`, or in another account, before you change `routes` in
> `wrangler.jsonc`.

### Settings in Cloudflare

The domain `zycode.dev` is registered at Cloudflare Registrar. The DNS zone is in the
same Cloudflare account, so you change no name server.

The settings in the table are in Cloudflare, not in this repository. If you move the
site to another account, make them again. All of them are free.

| Setting                          | Value                                                                              | Reason                                                                               |
| -------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------ |
| Custom Domain                    | `zycode.dev`, set in `wrangler.jsonc`                                              | Cloudflare makes the DNS record and uses the certificate of the zone.                |
| DNS record                       | `AAAA`, name `www`, content `100::`, proxied                                       | A redirect rule works only for a proxied record. A request never reaches `100::`.    |
| Redirect rule                    | From `www.zycode.dev` to `https://zycode.dev`. Status 301. The path and query stay. | The site has one address.                                                            |
| Always Use HTTPS                 | On                                                                                 | A request on port 80 gets a redirect to HTTPS.                                       |
| Minimum TLS version              | 1.2                                                                                | A client that supports only TLS 1.0 or 1.1 cannot connect.                           |
| 0-RTT                            | On                                                                                 | A client that returns connects faster. The site has no request that changes data.    |
| DNSSEC                           | On                                                                                 | Cloudflare Registrar sends the DS record to the registry. This takes up to two days. |
| Configuration rule               | `disable_rum` for all requests                                                     | See "No script from Cloudflare".                                                     |
| Mail records                     | Four DNS records                                                                   | See "No mail on the domain".                                                         |
| Workers Builds                   | See "Workers Builds settings".                                                     | A merge into `main` deploys the site.                                                |
| Registrar lock and WHOIS privacy | On (the default)                                                                   | Nobody can move the domain. The public WHOIS record does not show your contact data. |

The file `public/_headers` also sets `Strict-Transport-Security`. The top-level domain
`.dev` is on the HSTS preload list of the browsers, so a browser uses HTTPS for the site
before it sends a request.

### Workers Builds settings

These settings are in the Worker `zycode-web`, under **Settings** and **Build**. They are
not in this repository. If you move the site to another account, make them again.
Cloudflare applies a saved change to the next build.

| Setting                   | Value                                                | Reason                                                                             |
| ------------------------- | ---------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Git repository            | `zy1233/pi-python`                                   | The GitHub App of Cloudflare has access to this one repository.                    |
| Production branch         | `main`                                               | A merge into `main` deploys the site.                                              |
| Root directory            | `/web`                                               | The site is in a folder of a larger repository.                                    |
| Build command             | `npm run check && npm run build`                     | The `&&` stops at the first error.                                                 |
| Deploy command            | `npx wrangler deploy`                                | Wrangler uploads `dist/` and keeps the Custom Domain.                              |
| Build watch path          | Include `web/*`                                      | A change outside `web/` starts no build.                                           |
| Builds for other branches | Off                                                  | The site has one address. A branch gets no preview.                                |
| Build variables           | None                                                 | `SITE_URL` keeps its default value, `https://zycode.dev`.                          |
| Node.js                   | From `.node-version` (24)                            | Cloudflare reads the file. The web CI reads it too.                                |
| npm                       | From `packageManager` in `package.json` (11.19.1)    | Cloudflare and the web CI install this version. Both use the same npm.             |
| Build token               | Cloudflare made it when you connected the repository | The build needs a token to upload the files.                                       |

### No script from Cloudflare

A Cloudflare Free zone gets the Real User Monitoring (RUM) beacon by default. Cloudflare
adds the beacon script to each HTML page. The policy in `public/_headers` blocks that
script, and the browser shows an error in the console.

The configuration rule turns the beacon off. The page that a visitor gets is then the
page in this repository, and the page loads only files from `zycode.dev`.

To count the visitors, open the **Analytics** pages of the zone `zycode.dev` in the
Cloudflare dashboard. These numbers come from the request logs of Cloudflare. They need
no script.

To use the beacon, delete the configuration rule and change `script-src` in
`public/_headers`. Then open the page and make sure that the console shows no error.

### No mail on the domain

The domain `zycode.dev` sends and receives no mail. Four DNS records say this, and they
stop a forged message that uses the domain:

| Type | Name                      | Content                                | Reason                                         |
| ---- | ------------------------- | -------------------------------------- | ---------------------------------------------- |
| TXT  | `zycode.dev`              | `v=spf1 -all`                          | No server can send mail for the domain.        |
| TXT  | `_dmarc.zycode.dev`       | `v=DMARC1; p=reject; adkim=s; aspf=s;` | A receiver rejects a message that is forged.   |
| TXT  | `*._domainkey.zycode.dev` | `v=DKIM1; p=`                          | An empty key revokes each DKIM selector.       |
| MX   | `zycode.dev`              | `.`, priority 0                        | The domain accepts no mail (null MX, RFC 7505). |

To use mail on the domain, replace these four records with the records that the mail
service gives you. Do this before you send the first message. The DMARC policy `reject`
makes a receiver reject each message that does not pass the checks.

### Build for another address

The site address goes into the canonical links, the language links, the sitemap, and the
social card. The default address is `https://zycode.dev`. To build for another address,
set `SITE_URL`, then build:

```powershell
$env:SITE_URL = "http://localhost:4321"; npm run build
```

```bash
SITE_URL=http://localhost:4321 npm run build
```

Search engines must not index a preview address. For `*.workers.dev`, `*.pages.dev`,
and `localhost`, the build writes `Disallow: /` in `robots.txt` and adds a `noindex`
tag to each page.

> **Caution:** Remove `SITE_URL` from the environment before you run `npm run deploy`.
> If it is set, the build puts the wrong address and, for a preview address, a `noindex`
> tag on the real site.

## Check before you deploy

1. `npm run check` shows 0 errors.
2. `npm run build` completes.
3. Run `npm run preview`. Look at the home page and `/zh/` in the light theme and in the
   dark theme, at a width of 1440 pixels and at a width of 360 pixels.
4. Make sure that the page does not scroll sideways at a width of 320 pixels.

The web CI does steps 1 and 2 on each pull request that changes `web/`. Steps 3 and 4
need your eyes. Do them before you merge, because a merge deploys the site.
