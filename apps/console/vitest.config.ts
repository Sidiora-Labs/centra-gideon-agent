import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// Vitest config for the web app's unit/component tests. Kept separate from

// A junit report is emitted only when VITEST_JUNIT_OUTPUT_FILE names one, so a local
// `npm run test:web` is unchanged and CI keeps a machine-readable result after a failure.
const junitOutputFile = process.env.VITEST_JUNIT_OUTPUT_FILE

export default defineConfig({
  plugins: [react()],
  test: {
    reporters: junitOutputFile ? ['default', 'junit'] : ['default'],
    outputFile: junitOutputFile ? { junit: junitOutputFile } : undefined,
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/shared/testing/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
})
