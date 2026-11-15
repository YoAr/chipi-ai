import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The app is served at /app/ (the landing page owns "/").
// In dev, /api is proxied to the FastAPI server on :8010.
export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://localhost:8010' },
  },
})
