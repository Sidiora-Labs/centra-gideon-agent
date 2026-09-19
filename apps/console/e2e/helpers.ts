import { type Page, expect } from '@playwright/test'
import type { Theme } from './routes'


export async function seedTheme(page: Page, theme: Theme): Promise<void> {
  await page.addInitScript((t) => {
    try { localStorage.setItem('mode', t) } catch {   }
  }, theme)
  await page.emulateMedia({ colorScheme: theme })
}

export const SHELL_SELECTOR = 'nav[data-tour="rail"]'

export async function assertShellMounted(page: Page): Promise<void> {
  await expect(
    page.locator(SHELL_SELECTOR),
    `the app shell (${SHELL_SELECTOR}) is not mounted — this is the ONBOARDING screen, not\n` +
      `the route under test. The harness gateway is unreachable, unauthenticated or not\n` +
      `onboarded; any clean result measured here is meaningless. See playwright.config.ts.`,
  ).toBeVisible({ timeout: 10_000 })
}

export async function preloadRoute(page: Page, route: string): Promise<void> {
  await page.waitForFunction(
    () => typeof (window as unknown as { __gideon_preload_route?: unknown }).__gideon_preload_route === 'function',
    undefined,
    { timeout: 5_000 },
  )
  await page.evaluate(
    (target) => (window as unknown as { __gideon_preload_route: (path: string) => Promise<boolean> })
      .__gideon_preload_route(target),
    route,
  )
}

export async function gotoRoute(page: Page, route: string): Promise<void> {
  await page.goto(`/#/${route}`)
  await preloadRoute(page, route)
  await assertShellMounted(page)
  await page.evaluate(() => (document as unknown as { fonts?: { ready: Promise<unknown> } }).fonts?.ready)
  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => {   })
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
  await settleEntranceAnimations(page)
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
}

export async function settleEntranceAnimations(page: Page, timeout = 2_000): Promise<void> {
  await page
    .waitForFunction(
      () => {
        const midFade = Array.from(document.querySelectorAll<HTMLElement>('[style*="opacity"]')).some(
          (el) => {
            const v = Number.parseFloat(el.style.opacity)
            return Number.isFinite(v) && v > 0.01 && v < 0.99
          },
        )
        const running =
          typeof document.getAnimations === 'function' &&
          document.getAnimations().some((a) => a.playState === 'running')
        return !midFade && !running
      },
      undefined,
      { timeout },
    )
    .catch(() => {
    })
}

export async function assertMounted(page: Page, before: number, label: string): Promise<void> {
  const after = await page.evaluate(() => document.querySelectorAll('*').length)
  expect(after, `${label}: only ${after} elements — the app did not render`).toBeGreaterThan(80)
  expect(
    after,
    `${label}: element count did not grow (${before} → ${after}). The opener ran without\n` +
      `opening anything, so a clean axe result here would be meaningless.`,
  ).toBeGreaterThan(before)
}

export type OpenResult = true | { skip: string }

export interface Opener {
  label: string
  route: string
  open: (page: Page) => Promise<OpenResult>
}

async function clickRowBody(page: Page): Promise<OpenResult> {
  const row = page.locator('button.absolute.inset-0').first()
  if (!(await row.count())) return { skip: 'no list rows on this route — nothing to peek at (seed data to cover it)' }
  const box = await row.boundingBox()
  if (!box) return { skip: 'the first list row has no box (collapsed or off-screen)' }
  await page.mouse.click(box.x + Math.min(400, box.width / 2), box.y + box.height / 2)
  return true
}

export const OPENERS: Opener[] = [
  {
    label: 'command palette',
    route: 'chat',
    open: async (page) => {
      await page.keyboard.press('ControlOrMeta+k')
      return true
    },
  },
  {
    label: 'chat slash menu',
    route: 'chat',
    open: async (page) => {
      const cm = page.locator('[contenteditable="true"]').first()
      if (!(await cm.count())) return { skip: 'no contenteditable composer on #/chat' }
      await cm.click()
      await cm.pressSequentially('/')
      await cm.page().locator('[role="listbox"]').first()
        .waitFor({ state: 'visible', timeout: 8_000 })
        .catch(() => {   })
      return true
    },
  },
  { label: 'knowledge peek dock', route: 'knowledge', open: clickRowBody },
  { label: 'inbox peek dock', route: 'inbox', open: clickRowBody },
  {
    label: 'new project modal',
    route: 'projects',
    open: async (page) => {
      const btn = page.getByRole('button', { name: /new project/i }).first()
      if (!(await btn.count())) return { skip: 'no "New project" button on #/projects' }
      await btn.click()
      return true
    },
  },
]

export async function expectRouteScreenshot(page: Page, name: string): Promise<void> {
  await expect(page).toHaveScreenshot(`${name}.png`, {
    fullPage: true,
    animations: 'disabled',
    mask: [],
  })
}
