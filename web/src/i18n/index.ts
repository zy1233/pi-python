import { en } from './en';
import type { Messages } from './types';
import { zh } from './zh';

export type Locale = 'en' | 'zh';

export const locales: Locale[] = ['en', 'zh'];
export const messages: Record<Locale, Messages> = { en, zh };

/** The path of the home page of each language. Both end with a slash. */
export const homePath: Record<Locale, string> = { en: '/', zh: '/zh/' };

export function otherLocale(locale: Locale): Locale {
  return locale === 'en' ? 'zh' : 'en';
}

const escapeHtml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/**
 * Turn the inline markup of a message into HTML: `code` and **strong**, and
 * [text](url) for a link. The text is escaped first. Only strings of the message
 * files go through this function.
 */
export function md(text: string): string {
  return escapeHtml(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(
      /\[([^\]]+)\]\((https:\/\/[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>',
    );
}
