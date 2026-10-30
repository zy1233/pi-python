import type { APIRoute } from 'astro';
import { homePath, locales, messages } from '../i18n';

export const GET: APIRoute = ({ site }) => {
  const url = (path: string) => new URL(path, site).href;

  // Each page lists all of its language versions, as search engines ask.
  const alternates = [
    ...locales.map((locale) => `    <xhtml:link rel="alternate" hreflang="${messages[locale].hreflang}" href="${url(homePath[locale])}"/>`),
    `    <xhtml:link rel="alternate" hreflang="x-default" href="${url(homePath.en)}"/>`,
  ].join('\n');

  const entries = locales
    .map((locale) => `  <url>\n    <loc>${url(homePath[locale])}</loc>\n${alternates}\n  </url>`)
    .join('\n');

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
${entries}
</urlset>
`;

  return new Response(body, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
};
