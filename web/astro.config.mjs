// @ts-check
import { defineConfig, fontProviders } from 'astro/config';

// The public address of the site. It goes into canonical links, hreflang links,
// the sitemap and the social card. Set SITE_URL for a build that is not for the real
// site, for example a build that you open on your own computer. Do not deploy such a build.
//   PowerShell:  $env:SITE_URL = "http://localhost:4321"; npm run build
//   bash:        SITE_URL=http://localhost:4321 npm run build
const site = (process.env.SITE_URL ?? 'https://zycode.dev').replace(/\/+$/, '');

export default defineConfig({
  site,
  output: 'static',
  // /zh/ is the folder zh/index.html. Cloudflare serves it with a trailing slash.
  trailingSlash: 'always',
  build: { format: 'directory' },
  // Astro 7 strips the white space between inline elements by default (JSX rules).
  // Keep the normal HTML rules, so that `a <code>b</code> c` keeps its spaces.
  compressHTML: true,
  devToolbar: { enabled: false },
  i18n: {
    defaultLocale: 'en',
    locales: ['en', 'zh'],
    routing: { prefixDefaultLocale: false },
  },
  // Astro downloads these fonts when it builds, and it serves them from this site.
  // The page makes no request to a font service.
  fonts: [
    {
      provider: fontProviders.fontsource(),
      name: 'Schibsted Grotesk',
      cssVariable: '--font-sans',
      weights: ['400 800'],
      styles: ['normal'],
      subsets: ['latin'],
      fallbacks: ['system-ui', 'sans-serif'],
    },
    {
      provider: fontProviders.fontsource(),
      name: 'JetBrains Mono',
      cssVariable: '--font-mono',
      weights: ['400 700'],
      styles: ['normal'],
      subsets: ['latin'],
      fallbacks: ['ui-monospace', 'monospace'],
    },
  ],
});
