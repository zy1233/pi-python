import type { APIRoute } from 'astro';
import { messages } from '../../i18n';
import { getPublishedPosts, postPath } from '../../lib/blog';

const escapeXml = (value: string) =>
  value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');

export const GET: APIRoute = async ({ site }) => {
  const posts = await getPublishedPosts();
  const url = (path: string) => new URL(path, site).href;
  const feedUrl = url('/blog/rss.xml');
  const blogUrl = url('/blog/');

  const items = posts
    .map((post) => {
      const link = url(postPath(post.id));
      const categories = post.data.tags.map((tag) => `      <category>${escapeXml(tag)}</category>`).join('\n');
      return [
        '    <item>',
        `      <title>${escapeXml(post.data.title)}</title>`,
        `      <link>${escapeXml(link)}</link>`,
        `      <guid isPermaLink="true">${escapeXml(link)}</guid>`,
        `      <pubDate>${post.data.date.toUTCString()}</pubDate>`,
        `      <description>${escapeXml(post.data.description)}</description>`,
        ...(categories ? [categories] : []),
        '    </item>',
      ].join('\n');
    })
    .join('\n');

  const lastBuild = posts[0]?.data.date.toUTCString() ?? new Date().toUTCString();
  const body = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>${escapeXml(messages.en.blog.metaTitle)}</title>
    <link>${escapeXml(blogUrl)}</link>
    <description>${escapeXml(messages.en.blog.metaDescription)}</description>
    <lastBuildDate>${lastBuild}</lastBuildDate>
    <atom:link href="${escapeXml(feedUrl)}" rel="self" type="application/rss+xml"/>
${items}
  </channel>
</rss>
`;

  return new Response(body, { headers: { 'Content-Type': 'application/rss+xml; charset=utf-8' } });
};
