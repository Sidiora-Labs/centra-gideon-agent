import { test, expect, type Page, type Locator } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { gotoRoute } from './helpers'
import { SCRIPTED } from '../playwright.config'

const PROMPT = 'Open the desktop Session Map.'

async function assertMapPosition(rail: Locator, count: number) {
  const current = rail.locator('[data-session-marker][data-current="true"]')
  await expect(current).toHaveCount(1)
  await expect(current).toHaveAttribute('aria-current', 'location')
  await expect.poll(async () => rail.evaluate((element) => {
    const marks = Array.from(element.querySelectorAll('[data-session-marker]'))
    const index = marks.findIndex((mark) => mark.getAttribute('data-current') === 'true')
    return {
      count: marks.length,
      consistent: index >= 0 && element.querySelector('[role="status"]')?.textContent
        === `Message ${index + 1} of ${marks.length}`,
    }
  })).toEqual({ count, consistent: true })
}

async function sendTurn(page: Page, rail: Locator, previousCount: number) {
  const expectedCount = previousCount + 2
  const composer = page.getByRole('textbox', { name: 'Message input' })
  const prompt = `${PROMPT} Turn ${expectedCount / 2}.`
  await expect(rail.locator('[data-session-marker]')).toHaveCount(previousCount)
  await composer.fill(prompt)
  const send = page.getByRole('button', { name: 'Send message', exact: true })
  await expect(send).not.toHaveAttribute('aria-disabled', 'true')
  await send.click()
  const markers = rail.locator('[data-session-marker]')
  await expect(markers).toHaveCount(expectedCount, { timeout: 60_000 })
  await expect(markers.nth(previousCount)).toContainText(prompt)
  await expect(markers.nth(expectedCount - 1)).toContainText(SCRIPTED.reply, { timeout: 60_000 })
  await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0, { timeout: 60_000 })
  await expect(page.getByRole('button', { name: 'Regenerate', exact: true })).toBeVisible()
  await expect(rail).toHaveAttribute('data-session-map-state', 'open')
  await assertMapPosition(rail, expectedCount)
  return expectedCount
}

async function openPopulatedSessionMap(page: Page) {
  await gotoRoute(page, 'chat')
  const rail = page.getByRole('region', { name: 'Session map messages' })
  await sendTurn(page, rail, 0)
  return rail
}

function channel(value: number) {
  const normalized = value / 255
  return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
}

function luminance(rgb: string) {
  const [red, green, blue] = rgb.match(/[\d.]+/g)!.slice(0, 3).map(Number).map(channel)
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue
}

function contrast(first: string, second: string) {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

test.describe('desktop Session Map', () => {
  test.describe.configure({ timeout: 120_000 })

  test('rail-open state passes axe and mark tones meet non-text contrast', async ({ page }, testInfo) => {
    const rail = await openPopulatedSessionMap(page)
    const results = await new AxeBuilder({ page })
      .include('[data-session-map-state="open"]')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    await testInfo.attach('axe-session-map-open.json', {
      body: JSON.stringify(results.violations, null, 2),
      contentType: 'application/json',
    })
    expect(results.violations).toEqual([])

    const railBackground = await rail.evaluate((element) => getComputedStyle(element).backgroundColor)
    for (const tone of ['current', 'history']) {
      const mark = rail.locator(`[data-marker-tone="${tone}"] > span`).first()
      const markBackground = await mark.evaluate((element) => getComputedStyle(element).backgroundColor)
      expect(contrast(markBackground, railBackground), `${tone} mark contrast`).toBeGreaterThanOrEqual(3)
    }
  })

  test('waits for each new turn even when assistant replies repeat', async ({ page }) => {
    const rail = await openPopulatedSessionMap(page)
    let completedMarkers = 2
    for (let turn = 0; turn < 2; turn++) {
      completedMarkers = await sendTurn(page, rail, completedMarkers)
    }
    await expect(rail.locator('[data-session-marker]')).toHaveCount(6)
    await expect(rail.getByRole('status', { name: 'Session map position' })).toHaveText(/Message \d+ of 6/)
  })

  test('walks the rail with Arrow keys, Home, and End', async ({ page }) => {
    const rail = await openPopulatedSessionMap(page)
    const markers = rail.locator('[data-session-marker]')
    await expect(markers).toHaveCount(2)

    await markers.first().focus()
    await page.keyboard.press('ArrowDown')
    await expect(markers.last()).toBeFocused()
    await page.keyboard.press('Home')
    await expect(markers.first()).toBeFocused()
    await page.keyboard.press('End')
    await expect(markers.last()).toBeFocused()
    await page.keyboard.press('ArrowUp')
    await expect(markers.first()).toBeFocused()
    await assertMapPosition(rail, 2)
  })
})
