import { defineDocs, defineConfig, frontmatterSchema } from 'fumadocs-mdx/config';
import { z } from 'zod';

export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    schema: frontmatterSchema.extend({
      icon: z.string().optional(),
      full: z.boolean().optional(),
    }),
  },
});

export default defineConfig({
  mdxOptions: {
    // Additional MDX options can be configured here
  },
});
