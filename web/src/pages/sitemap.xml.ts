import type { APIRoute } from 'astro';
import { homePath, locales, messages } from '../i18n';
import { blogPath, findTranslation, getPublishedPosts, isoDate, postPath } from '../lib/blog';

export const GET: APIRoute = async ({ site }) => {
  const url = (path: string) => new URL(path, site).href;

  const bilingualEntries = (paths: Record<'en' | 'zh', string>) => {
    const alternates = [
      ...locales.map(
        (locale) => `    <xhtml:link rel="alternate" hreflang="${messages[locale].hreflang}" href="${url(paths[locale])}"/>`,
      ),
      `    <xhtml:link rel="alternate" hreflang="x-default" href="${url(paths.en)}"/>`,
    ].join('\n');
    return locales.map((locale) => `  <url>\n    <loc>${url(paths[locale])}</loc>\n${alternates}\n  </url>`);
  };

  const posts = await getPublishedPosts();
  const postEntries = posts.map((post) => {
    const selfUrl = url(postPath(post.id));
    const lastmod = isoDate(post.data.updated ?? post.data.date);
    const counterpart = findTranslation(post, posts);
    const lines = [`  <url>`, `    <loc>${selfUrl}</loc>`, `    <lastmod>${lastmod}</lastmod>`];
    if (counterpart) {
      const enUrl = post.data.lang === 'en' ? selfUrl : url(postPath(counterpart.id));
      const zhUrl = post.data.lang === 'zh' ? selfUrl : url(postPath(counterpart.id));
      lines.push(
        `    <xhtml:link rel="alternate" hreflang="${messages.en.hreflang}" href="${enUrl}"/>`,
        `    <xhtml:link rel="alternate" hreflang="${messages.zh.hreflang}" href="${zhUrl}"/>`,
        `    <xhtml:link rel="alternate" hreflang="x-default" href="${enUrl}"/>`,
      );
    }
    lines.push(`  </url>`);
    return lines.join('\n');
  });

  const entries = [...bilingualEntries(homePath), ...bilingualEntries(blogPath), ...postEntries].join('\n');

  const body = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
${entries}
</urlset>
`;

  return new Response(body, { headers: { 'Content-Type': 'application/xml; charset=utf-8' } });
};
