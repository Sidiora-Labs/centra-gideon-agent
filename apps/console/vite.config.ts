import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import { consoleArtifacts } from './tooling/vite/artifacts'
import { gatewaySession } from './tooling/vite/gateway'
import { widgetRuntime } from './tooling/vite/widgetRuntime.ts'
import { thirdPartyNotices } from './tooling/thirdPartyNotices.mjs'

const backend = `http://127.0.0.1:${process.env.GIDEON_PORT || 10000}`
const consoleRoot = dirname(fileURLToPath(import.meta.url))
const notices = thirdPartyNotices(consoleRoot, { repoRoot: resolve(consoleRoot, '../..') })

export default defineConfig({
  // Shared assistant TypeScript uses the console transform settings in this build.
  // The assistant keeps its separate Expo configuration for native builds.
  tsconfig: resolve(consoleRoot, 'tsconfig.json'),
  plugins: [react(), tailwindcss(), gatewaySession(backend), consoleArtifacts(consoleRoot), widgetRuntime(), notices.plugin()],
  server: {
    port: 3100,
    proxy: {
      '/api': { target: backend, changeOrigin: true, ws: true },
      '/artifacts/serve': { target: backend, changeOrigin: true },
      '/apps': { target: backend, changeOrigin: true },
    },
  },
  worker: { plugins: () => [notices.workerPlugin()] },
  build: { outDir: 'dist' },
})
