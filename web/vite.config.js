import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const repoRoot = path.resolve(fileURLToPath(new URL('.', import.meta.url)), '..')

// https://vite.dev/config/
export default defineConfig({
  envPrefix: ['VITE_', 'MAPBOX_', 'STYLE_', 'ACCESS_'],
  plugins: [react()],
  server: {
    fs: {
      allow: [repoRoot],
    },
  },
})
