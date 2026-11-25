#!/usr/bin/env node
/**
 * Convert a GitHub Issue or workflow_dispatch payload into a Markdown post
 * under `web/src/content/blog/<slug>.md`.
 *
 * All inputs are read from environment variables to avoid shell interpolation.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const blogDir = path.resolve(__dirname, '..', 'src', 'content', 'blog');
const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function slugify(text) {
  return text
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 72);
}

/**
 * Parse GitHub Issue Form sections (`### Heading\n\nvalue`).
 * Returns null if the body is plain Markdown instead of an Issue Form submission.
 */
function parseIssueForm(body) {
  const normalized = body.replace(/\r\n/g, '\n');
  if (!/^###\s+(Slug|Language|Description|Tags|Content)\s*$/m.test(normalized)) {
    return null;
  }
  const sections = new Map();
  const headingRe = /^###\s+([^\n]+)\s*$/gm;
  const matches = [...normalized.matchAll(headingRe)];
  for (let i = 0; i < matches.length; i += 1) {
    const key = matches[i][1].trim().toLowerCase();
    const start = matches[i].index + matches[i][0].length;
    const end = i + 1 < matches.length ? matches[i + 1].index : normalized.length;
    const raw = normalized.slice(start, end).trim();
    sections.set(key, raw === '_No response_' ? '' : raw);
  }
  return sections;
}

function firstParagraph(markdown) {
  const plain = markdown
    .replace(/\r\n/g, '\n')
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .find((block) => block && !block.startsWith('#') && !block.startsWith('```'));
  if (!plain) return '';
  return plain
    .replace(/\[([^\]]+)]\([^)]+\)/g, '$1')
    .replace(/[`*_>#]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 180);
}

function detectLang(text) {
  return /[\p{Script=Han}]/u.test(text) ? 'zh' : 'en';
}

function main() {
  const mode = process.env.BLOG_MODE || 'issue';
  const today = new Date().toISOString().slice(0, 10);

  let title = (process.env.BLOG_TITLE || '').trim();
  let slug = (process.env.BLOG_SLUG || '').trim().toLowerCase();
  let lang = (process.env.BLOG_LANG || '').trim().toLowerCase();
  let description = (process.env.BLOG_DESCRIPTION || '').trim();
  let rawTags = (process.env.BLOG_TAGS || '').trim();
  let translation = (process.env.BLOG_TRANSLATION || '').trim();
  let content = (process.env.BLOG_BODY || '').replace(/\r\n/g, '\n').trim();
  const issueNumber = (process.env.BLOG_ISSUE_NUMBER || '').trim();

  if (mode === 'issue') {
    const form = parseIssueForm(content);
    if (form) {
      slug = (form.get('slug') || slug).trim().toLowerCase();
      lang = (form.get('language') || lang).trim().toLowerCase();
      description = (form.get('description') || description).trim();
      rawTags = (form.get('tags') || rawTags).trim();
      translation = (form.get('translation slug (optional)') || form.get('translation') || translation).trim();
      content = (form.get('content') || '').trim();
    }
  }

  if (!title) {
    throw new Error('Post title is empty.');
  }
  if (!content) {
    throw new Error('Post body is empty.');
  }

  if (!lang) {
    lang = detectLang(`${title}\n${content}`);
  }
  if (lang !== 'en' && lang !== 'zh') {
    throw new Error(`Unsupported language "${lang}". Expected "en" or "zh".`);
  }

  if (!slug) {
    const derived = slugify(title);
    slug = derived || (issueNumber ? `post-${issueNumber}` : `post-${today}`);
  }
  if (!SLUG_RE.test(slug) || slug.length > 80) {
    throw new Error(`Invalid slug "${slug}". Use lowercase ASCII letters, digits, and hyphens only.`);
  }

  if (!description) {
    description = firstParagraph(content) || title;
  }

  const tags = rawTags
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);

  const lines = [
    '---',
    `title: ${JSON.stringify(title)}`,
    `description: ${JSON.stringify(description)}`,
    `date: ${today}`,
    `lang: ${lang}`,
  ];
  if (tags.length > 0) {
    lines.push('tags:');
    for (const tag of tags) {
      lines.push(`  - ${JSON.stringify(tag)}`);
    }
  }
  if (translation) {
    lines.push(`translation: ${JSON.stringify(translation)}`);
  }
  lines.push('---', '', content, '');

  fs.mkdirSync(blogDir, { recursive: true });
  const filePath = path.join(blogDir, `${slug}.md`);
  fs.writeFileSync(filePath, lines.join('\n'), 'utf8');

  const githubOutput = process.env.GITHUB_OUTPUT;
  if (githubOutput) {
    fs.appendFileSync(githubOutput, `slug=${slug}\nfile=web/src/content/blog/${slug}.md\n`, 'utf8');
  }

  console.log(`Generated web/src/content/blog/${slug}.md`);
}

try {
  main();
} catch (error) {
  console.error(`error: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
}
