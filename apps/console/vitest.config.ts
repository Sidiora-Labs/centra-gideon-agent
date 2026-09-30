import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { widgetRuntime } from './tooling/vite/widgetRuntime.ts'

const SWITCHED_OFF_LIVE_REQUESTED = process.env.GIDEON_E2E_FEATURE_STATES === '1'
  || process.argv.some((argument) => argument.includes('switchedOffIsSaid.live'))

// Vitest config for the web app's unit/component tests. Kept separate from
export default defineConfig({
  plugins: [react(), widgetRuntime()],
  test: {
    coverage: { provider: 'v8', reporter: ['text-summary', 'json', 'json-summary'] },
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/shared/testing/setup.ts'],
    include: [
      'src/**/*.{test,spec}.{ts,tsx}',
      ...(SWITCHED_OFF_LIVE_REQUESTED ? ['e2e/switchedOffIsSaid.live.tsx'] : []),
    ],
    pool: 'forks',
    maxWorkers: 4,
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
})
