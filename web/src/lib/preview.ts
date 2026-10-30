/**
 * True when the site is built for a preview address, such as `zycode-web.<name>.workers.dev`.
 * A preview must stay out of search engines, so the real domain (`zycode.dev`, the
 * default when SITE_URL is not set) gets all the credit.
 */
export function isPreview(site: URL | string | undefined): boolean {
  if (!site) return false;
  const host = new URL(site).hostname;
  return host.endsWith('.workers.dev') || host.endsWith('.pages.dev') || host === 'localhost' || host === '127.0.0.1';
}
