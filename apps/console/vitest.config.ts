import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Vitest config for the web app's unit/component tests. Kept separate from
export default defineConfig({
  plugins: [react()],
  test: {
    coverage: { provider: 'v8', reporter: ['text-summary', 'json', 'json-summary'] },
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/shared/testing/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    pool: 'forks',
    maxWorkers: 4,
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
})
