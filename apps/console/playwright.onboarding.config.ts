import { defineConfig } from '@playwright/test'
import base from './playwright.config'

export default defineConfig({
  ...base,
  fullyParallel: false,
  workers: 1,
  testIgnore: [],
  projects: base.projects?.map((project) => project.name === 'chromium'
    ? { ...project, testMatch: /onboardingGeometry\.spec\.ts$/ } : project),
  testMatch: /(?:auth\.setup|onboardingGeometry\.spec)\.ts$/,
})
