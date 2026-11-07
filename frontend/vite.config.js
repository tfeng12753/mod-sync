import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      // The dev-only mock (VITE_USE_MOCK=1) imports ../fixtures/menu.json from the repo root.
      allow: [fileURLToPath(new URL('.', import.meta.url)), fileURLToPath(new URL('../fixtures', import.meta.url))],
    },
  },
})
