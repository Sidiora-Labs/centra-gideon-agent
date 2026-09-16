import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import { consoleArtifacts } from './tooling/vite/artifacts'
import { gatewaySession } from './tooling/vite/gateway'

const backend = `http://127.0.0.1:${process.env.GIDEON_PORT || 10000}`
const consoleRoot = dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  plugins: [react(), tailwindcss(), gatewaySession(backend), consoleArtifacts(consoleRoot)],
  server: {
    port: 3100,
    proxy: {
      '/api': { target: backend, changeOrigin: true, ws: true },
      '/apps': { target: backend, changeOrigin: true },
    },
  },
  build: { outDir: 'dist' },
})
