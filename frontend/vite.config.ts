import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * 本番ビルドは nginx が静的配信するため base は '/' のまま。
 * `npm run dev` のときだけ /api を nginx(=backend) へプロキシして CORS を避ける。
 */
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8090',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});
