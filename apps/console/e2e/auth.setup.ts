import { test as setup, expect } from '@playwright/test'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import { SHELL_SELECTOR } from './helpers'


const BASE = process.env.PW_BASE_URL || `http://localhost:${process.env.PW_PORT || 4318}`
const TOKEN = process.env.PW_TOKEN || ''
const OUT = process.env.STORAGE_STATE || 'e2e/.auth/state.json'

setup('authenticate', async ({ page, context }) => {
  mkdirSync(dirname(OUT), { recursive: true })
  writeFileSync(OUT, JSON.stringify({ cookies: [], origins: [] }, null, 2))

  setup.skip(!TOKEN, 'PW_TOKEN not set — no gateway token to seed the harness session with')

  await page.goto(`${BASE}/?token=${encodeURIComponent(TOKEN)}`)
  await page.waitForLoadState('networkidle').catch(() => {})

  await page.goto(`${BASE}/#/dashboard`)
  await expect(
    page.locator(SHELL_SELECTOR),
    `the app shell never mounted at ${BASE}. The gateway is unreachable, unauthenticated,\n` +
      `or not onboarded — every route would render the onboarding screen and axe would\n` +
      `report a clean tree for a surface no user ever sees.`,
  ).toBeVisible({ timeout: 15_000 })

  await context.storageState({ path: OUT })
})
