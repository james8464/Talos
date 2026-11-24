import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const here = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': here + 'src',
      '@renderer': here + 'src/renderer',
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  server: { port: 5178 },
});
