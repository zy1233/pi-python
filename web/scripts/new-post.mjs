#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { stdin as input, stdout as output } from 'node:process';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const blogDir = path.resolve(__dirname, '..', 'src', 'content', 'blog');

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === '--help' || token === '-h') {
      args.help = true;
    } else if (token === '--draft') {
      args.draft = true;
    } else if (token === '--force') {
      args.force = true;
    } else if (token.startsWith('--')) {
      const key = token.slice(2);
      const next = argv[i + 1];
      if (!next || next.startsWith('--')) {
        throw new Error(`Missing value for --${key}`);
      }
      args[key] = next;
      i += 1;
    } else if (!args.title) {
      args.title = token;
    }
  }
  return args;
}

function slugify(text) {
  return text
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
}

function todayUtc() {
  return new Date().toISOString().slice(0, 10);
}

function printHelp() {
  console.log(`Create a new Markdown blog post under web/src/content/blog/.

Usage:
  npm run post:new
  npm run post:new -- --title "My Post" --lang en --slug my-post --desc "Short summary" --tags "release,acp"

Options:
  --title <text>        Article title (required)
  --slug <slug>         URL slug, e.g. my-post (lowercase letters, digits, hyphens)
  --lang <en|zh>        Article language: en or zh (default: zh)
  --desc <text>         Short summary shown in list, RSS, and social previews
  --tags <a,b>          Comma-separated tags (optional)
  --date <YYYY-MM-DD>   Publish date in UTC (default: today)
  --translation <slug>  Optional slug of the counterpart translation post
  --draft               Mark as draft: true (visible in dev, omitted in production)
  --force               Overwrite an existing file with the same slug
  -h, --help            Show this message
`);
}

function buildMarkdown({ title, description, date, lang, tags, draft, translation }) {
  const lines = [
    '---',
    `title: ${JSON.stringify(title)}`,
    `description: ${JSON.stringify(description)}`,
    `date: ${date}`,
    `lang: ${lang}`,
  ];
  if (tags.length > 0) {
    lines.push('tags:');
    for (const tag of tags) {
      lines.push(`  - ${JSON.stringify(tag)}`);
    }
  }
  if (draft) {
    lines.push('draft: true');
  }
  if (translation) {
    lines.push(`translation: ${JSON.stringify(translation)}`);
  }
  lines.push('---', '', '');
  return lines.join('\n');
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    printHelp();
    return;
  }

  const interactive = !args.title && input.isTTY;
  let rl;
  try {
    const ask = async (question, fallback = '') => {
      if (!rl) rl = readline.createInterface({ input, output });
      const suffix = fallback ? ` [${fallback}]` : '';
      const answer = (await rl.question(`${question}${suffix}: `)).trim();
      return answer || fallback;
    };

    const title = (args.title ?? (interactive ? await ask('Title') : '')).trim();
    if (!title) {
      throw new Error('Title is required. Run with --help for usage.');
    }

    const rawLang = (args.lang ?? (interactive ? await ask('Language (zh/en)', 'zh') : 'zh')).trim().toLowerCase();
    if (rawLang !== 'en' && rawLang !== 'zh') {
      throw new Error(`Unsupported language "${rawLang}". Expected "en" or "zh".`);
    }

    const defaultSlug = slugify(title) || `post-${todayUtc()}`;
    const slug = (args.slug ?? (interactive ? await ask('Slug (URL segment)', defaultSlug) : defaultSlug)).trim().toLowerCase();
    if (!SLUG_RE.test(slug) || slug.length > 80) {
      throw new Error(`Invalid slug "${slug}". Use lowercase ASCII letters, digits, and hyphens only.`);
    }

    const description = (
      args.desc ??
      args.description ??
      (interactive ? await ask('Description (1-2 sentences)', title) : title)
    ).trim();

    const rawTags = args.tags ?? (interactive ? await ask('Tags (comma-separated, optional)', '') : '');
    const tags = rawTags
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean);

    const date = (args.date ?? todayUtc()).trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      throw new Error(`Invalid date "${date}". Use YYYY-MM-DD.`);
    }

    const translation = (args.translation ?? '').trim() || undefined;
    const draft = Boolean(args.draft);

    fs.mkdirSync(blogDir, { recursive: true });
    const targetPath = path.join(blogDir, `${slug}.md`);
    if (fs.existsSync(targetPath) && !args.force) {
      throw new Error(`Post already exists: ${targetPath} (pass --force to overwrite)`);
    }

    const content = buildMarkdown({ title, description, date, lang: rawLang, tags, draft, translation });
    fs.writeFileSync(targetPath, content, 'utf8');

    console.log(`Created ${path.relative(process.cwd(), targetPath)}`);
    console.log(`URL: /blog/${slug}/`);
    console.log('Next: edit the Markdown file, preview with `npm run dev`, then commit & push to publish.');
  } finally {
    rl?.close();
  }
}

main().catch((error) => {
  console.error(`error: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
