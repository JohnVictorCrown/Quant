import { fileURLToPath } from 'url';
import { join, dirname } from 'path';
import { skeleton } from '@skeletonlabs/tw-plugin';

const __dirname = dirname(fileURLToPath(import.meta.url));

export default {
  content: [
    './index.html',
    './src/**/*.{html,js,svelte,ts}',
    join(__dirname, 'node_modules/@skeletonlabs/skeleton/**/*.{html,js,svelte,ts}')
  ],
  theme: { extend: {} },
  plugins: [skeleton({ themes: { preset: ['wintry'] } })]
};
