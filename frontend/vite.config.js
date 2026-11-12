import { defineConfig, loadEnv } from 'vite';
import preact from '@preact/preset-vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), ''); // reads frontend/.env; not exposed to browser code
  return {
    plugins: [preact()],
    server: { proxy: { '/api': env.API_PROXY_TARGET || 'http://localhost:8000' } },
  };
});
