import type { APIRoute } from 'astro';
import { isPreview } from '../lib/preview';

export const GET: APIRoute = ({ site }) => {
  // A preview address (for example *.workers.dev) is closed to crawlers.
  const body = isPreview(site)
    ? 'User-agent: *\nDisallow: /\n'
    : `User-agent: *\nAllow: /\n\nSitemap: ${new URL('/sitemap.xml', site).href}\n`;

  return new Response(body, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
};
