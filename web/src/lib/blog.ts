import { getCollection, type CollectionEntry } from 'astro:content';
import type { Locale } from '../i18n';

export type BlogPost = CollectionEntry<'blog'>;

/** The path of the blog index for each language. Both end with a slash. */
export const blogPath: Record<Locale, string> = { en: '/blog/', zh: '/zh/blog/' };

/** Canonical URL path for a single blog post. */
export function postPath(id: string): string {
  return `/blog/${id}/`;
}

/**
 * Return published blog posts ordered newest-first.
 * Draft posts are visible in local `astro dev` preview and omitted from production builds.
 */
export async function getPublishedPosts(): Promise<BlogPost[]> {
  const posts = await getCollection('blog', ({ data }) => (import.meta.env.PROD ? !data.draft : true));
  return posts.sort((a, b) => {
    const diff = b.data.date.getTime() - a.data.date.getTime();
    return diff !== 0 ? diff : a.id.localeCompare(b.id);
  });
}

/**
 * Format a YAML date (`YYYY-MM-DD`, parsed as UTC midnight) without timezone drift.
 */
export function formatDate(date: Date, locale: Locale): string {
  return new Intl.DateTimeFormat(locale === 'zh' ? 'zh-CN' : 'en-US', {
    year: 'numeric',
    month: locale === 'zh' ? 'numeric' : 'short',
    day: 'numeric',
    timeZone: 'UTC',
  }).format(date);
}

/** Format an ISO `YYYY-MM-DD` string in UTC for `<time datetime>` and monospace ledger rows. */
export function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** Estimate reading time in whole minutes for mixed CJK and Latin Markdown bodies. */
export function estimateReadingMinutes(body: string | undefined): number {
  if (!body) return 1;
  const plain = body
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`[^`]*`/g, ' ')
    .replace(/!\[[^\]]*]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)]\([^)]*\)/g, '$1')
    .replace(/[#>*_~\-|]/g, ' ');
  const cjkCount = (plain.match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/gu) ?? []).length;
  const latinWords = plain
    .replace(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/gu, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean).length;
  const minutes = cjkCount / 350 + latinWords / 220;
  return Math.max(1, Math.round(minutes));
}

/** Look up the counterpart translation post if `post.data.translation` points to a published post. */
export function findTranslation(post: BlogPost, posts: BlogPost[]): BlogPost | undefined {
  const target = post.data.translation;
  if (!target) return undefined;
  return posts.find((candidate) => candidate.id === target);
}
