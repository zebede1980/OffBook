import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit({ adapter: adapter({ fallback: 'index.html' }) })],
  // 8092, not 8000: SillyTavern already holds 8000 on the dev PC
  server: { proxy: { '/api': 'http://127.0.0.1:8092' } }
});
