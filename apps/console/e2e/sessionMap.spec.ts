import { test, expect, type Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { gotoRoute } from './helpers'
import { SCRIPTED } from '../playwright.config'

const PROMPT = 'Open the desktop Session Map.'

async function openPopulatedSessionMap(page: Page) {
  await gotoRoute(page, 'chat')
  const composer = page.getByRole('textbox', { name: 'Message input' })
  await composer.click()
  await composer.pressSequentially(PROMPT)
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(page.getByText(SCRIPTED.reply, { exact: false }).first()).toBeVisible({ timeout: 60_000 })
  const rail = page.getByRole('region', { name: 'Session map messages' })
  await expect(rail).toHaveAttribute('data-session-map-state', 'open')
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
  })
})
