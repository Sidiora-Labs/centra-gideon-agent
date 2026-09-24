import { test, expect, type Page } from '@playwright/test'
import { settleEntranceAnimations } from './helpers'

const VIEWPORTS = [
  { width: 360, height: 740 },
  { width: 640, height: 800 },
  { width: 768, height: 1024 },
  { width: 1280, height: 900 },
]

async function checkStep(page: Page, title: string) {
  const active = page.locator('li[aria-current="step"]')
  const heading = active.getByRole('heading', { level: 2, name: title, exact: true })
  await expect(heading).toBeFocused()
  await expect(heading).toBeInViewport()
  await settleEntranceAnimations(page)
  const geometry = await active.evaluate((row) => {
    const body = row.querySelector<HTMLElement>('[class*="sm:ml-"]')!
    const style = getComputedStyle(body)
    const bounds = row.getBoundingClientRect()
    const controls = Array.from(row.querySelectorAll<HTMLElement>('input, button, select'))
      .filter((element) => element.getBoundingClientRect().width > 0)
    return {
      viewport: innerWidth,
      width: bounds.width,
      left: bounds.left,
      right: bounds.right,
      scrollWidth: body.scrollWidth,
      clientWidth: body.clientWidth,
      indent: parseFloat(style.marginLeft) - parseFloat(style.marginRight),
      clipped: controls.filter((element) => {
        const box = element.getBoundingClientRect()
        return box.left < bounds.left - 1 || box.right > bounds.right + 1
      }).map((element) => element.getAttribute('aria-label') || element.textContent),
    }
  })
  expect(geometry.width).toBeGreaterThan(200)
  expect(geometry.left).toBeGreaterThanOrEqual(0)
  expect(geometry.right).toBeLessThanOrEqual(geometry.viewport + 1)
  expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1)
  expect(geometry.clipped).toEqual([])
  if (geometry.viewport < 640) expect(Math.abs(geometry.indent)).toBeLessThan(1)
  else expect(geometry.indent).toBeGreaterThan(20)
}

for (const viewport of VIEWPORTS) {
  test(`onboarding geometry and heading focus at ${viewport.width}×${viewport.height}`, async ({ page }, testInfo) => {
    test.setTimeout(90_000)
    await page.setViewportSize(viewport)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    const configResponse = await page.request.get('/api/dashboard/config')
    expect(configResponse.ok()).toBe(true)
    const saved = await configResponse.json()
    const progressResponse = await page.request.get('/api/onboarding')
    expect(progressResponse.ok()).toBe(true)
    const progress = await progressResponse.json()
    try {
      expect((await page.request.put('/api/dashboard/config', { data: { user_name: '' } })).ok()).toBe(true)
      expect((await page.request.post('/api/onboarding/state', { data: { step: 'name' } })).ok()).toBe(true)
      await page.goto('/#/onboarding')
      await checkStep(page, 'Your name')
      await page.getByRole('textbox', { name: 'Your name', exact: true }).fill('Alexandra Montgomery')
      await page.getByRole('button', { name: 'Continue', exact: true }).click()
      const importStep = page.locator('li[aria-current="step"]')
      await expect(importStep.getByRole('button', { name: /^(Skip this|Continue)$/ })).toBeVisible()
      await checkStep(page, 'Bring your setup over')
      await importStep.getByRole('button', { name: /^(Skip this|Continue)$/ }).click()
      await expect(page.locator('li[aria-current="step"]').getByRole('button', { name: 'Set up later', exact: true })).toBeVisible()
      await checkStep(page, 'Essential apps')
      await page.locator('li[aria-current="step"]').getByRole('button', { name: 'Set up later', exact: true }).click()
      await checkStep(page, 'Try one')
      await page.locator('li[aria-current="step"]').getByRole('button', { name: 'Continue', exact: true }).click()
      await checkStep(page, 'All set')
      const scroll = page.locator('[data-onboarding-scroll]')
      const metrics = await scroll.evaluate((element) => {
        const box = element.getBoundingClientRect()
        element.scrollTop = element.scrollHeight
        return { height: box.height, viewport: innerHeight, scrollTop: element.scrollTop,
          maximum: element.scrollHeight - element.clientHeight, overflow: getComputedStyle(element).overflowY }
      })
      expect(metrics.height).toBeLessThanOrEqual(metrics.viewport + 1)
      expect(metrics.overflow).toBe('auto')
      expect(metrics.scrollTop).toBeCloseTo(metrics.maximum, 0)
      await expect(page.getByRole('button', { name: /Start using/ })).toBeInViewport()
      await testInfo.attach('ready-screen', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' })
      await page.getByRole('button', { name: 'Go back to step 1: Your name', exact: true }).click()
      await checkStep(page, 'Your name')
    } finally {
      await page.goto('about:blank')
      expect((await page.request.put('/api/dashboard/config', { data: { user_name: saved.user_name } })).ok()).toBe(true)
      expect((await page.request.post('/api/onboarding/state', {
        data: { step: progress.step, essentials: progress.essentials, first_success: progress.first_success },
      })).ok()).toBe(true)
    }
  })
}
