import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';

/**
 * Blog posts live as Markdown files under `src/content/blog/`.
 * Files starting with `_` (such as `_template.md`) are ignored.
 */
const blog = defineCollection({
  loader: glob({ pattern: '**/[^_]*.{md,mdx}', base: './src/content/blog' }),
  schema: z.object({
    title: z.string().min(1),
    description: z.string().min(1),
    date: z.coerce.date(),
    updated: z.coerce.date().optional(),
    lang: z.enum(['en', 'zh']),
    tags: z.array(z.string().min(1)).default([]),
    author: z.string().min(1).default('zycode'),
    draft: z.boolean().default(false),
    /** Optional slug (file id without extension) of the counterpart translation post. */
    translation: z.string().min(1).optional(),
  }),
});

export const collections = { blog };
