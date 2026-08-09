import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { VitePWA } from 'vite-plugin-pwa';

// VITE_SUPABASE_ANON_KEY is intentionally NOT required here: a missing key
// disables the savings leaderboard at runtime (see src/lib/supabase.ts) rather
// than failing the build, so the package/app stays publishable without it.
export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'OpenJarvis',
        short_name: 'Jarvis',
        description: 'On-device AI assistant',
        theme_color: '#161618',
        background_color: '#161618',
        display: 'standalone',
        icons: [
          { src: 'pwa-192x192.png', sizes: '192x192', type: 'image/png' },
          { src: 'pwa-512x512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        navigateFallbackDenylist: [/^\/v1\//, /^\/health/, /^\/dashboard/, /^\/api\//],
      },
    }),
  ],
  build: {
    outDir: '../src/openjarvis/server/static',
    emptyOutDir: true,
    minify: 'esbuild',
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom'],
          markdown: ['react-markdown', 'rehype-highlight', 'remark-gfm'],
          charts: ['recharts'],
          router: ['react-router'],
        },
      },
    },
  },
  server: {
    port: 5173,
    // Listen on all local interfaces so both http://127.0.0.1:5173 and
    // http://localhost:5173 work. Proxy target stays on 127.0.0.1 because
    // `jarvis serve --host 127.0.0.1` is IPv4-only — `localhost`→::1 breaks it.
    host: true,
    // Force HMR websocket to 127.0.0.1 — browser on 127.0.0.1 otherwise
    // tries ws://localhost and fails on Windows IPv6 localhost.
    hmr: {
      host: '127.0.0.1',
      protocol: 'ws',
      clientPort: 5173,
    },
    proxy: {
      // Always target 127.0.0.1 (IPv4). `localhost` can resolve to ::1 while
      // `jarvis serve --host 127.0.0.1` is IPv4-only → proxy 500s.
      // ws: true is required for /v1/agents/events WebSocket upgrades.
      '/v1': {
        target: process.env.VITE_API_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
      '/health': {
        target: process.env.VITE_API_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        target: process.env.VITE_API_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/dashboard': {
        target: process.env.VITE_API_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
