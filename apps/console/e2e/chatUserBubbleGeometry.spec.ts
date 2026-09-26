import { test, expect } from '@playwright/test'
import { gotoRoute } from './helpers'

test('a persisted user message keeps a natural bubble width on desktop and mobile', async ({ page }) => {
  const prompt = 'Check the release status.'
  await gotoRoute(page, 'chat/new')
  const composer = page.getByRole('textbox', { name: 'Message input' })
  await composer.click()
  await composer.pressSequentially(prompt)
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await page.waitForURL(url => url.hash.startsWith('#/chat/') && url.hash !== '#/chat/new')
  const bubble = page.locator('.gideon-chat-user').filter({ hasText: prompt }).first()
  await expect(bubble).toBeVisible()
  await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0, { timeout: 60_000 })
  await page.reload()
  await expect(bubble).toBeVisible()

  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 })
    const geometry = await bubble.evaluate((element, target) => {
      const box = element.getBoundingClientRect()
      const parent = element.parentElement!.getBoundingClientRect()
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
      let text = walker.nextNode()
      while (text && !text.textContent?.includes(target)) text = walker.nextNode()
      if (!text) throw Error('Persisted user text missing')
      const range = document.createRange()
      range.selectNodeContents(text)
      return { width: box.width, parentWidth: parent.width,
        rightGap: Math.abs(parent.right - box.right), lines: range.getClientRects().length,
        overflow: element.scrollWidth > element.clientWidth + 1 }
    }, prompt)
    expect(geometry.width, `${width}px bubble collapsed`).toBeGreaterThan(180)
    expect(geometry.width, `${width}px bubble fills the transcript`).toBeLessThan(geometry.parentWidth * (width < 640 ? 0.92 : 0.8) + 2)
    expect(geometry.lines, `${width}px short user message wrapped`).toBe(1)
    expect(geometry.rightGap, `${width}px bubble lost right alignment`).toBeLessThan(2)
    expect(geometry.overflow, `${width}px bubble clipped its text`).toBe(false)
    if (width === 390) {
      const viewport = page.locator('[data-slot="aui_thread-viewport"]')
      await viewport.evaluate((element) => { element.scrollTop = 0 })
      const launcher = page.getByRole('button', { name: 'Open session map' })
      await expect(launcher).toBeVisible()
      const clearOfMessage = await bubble.evaluate((element) => {
        const message = element.getBoundingClientRect()
        const button = document.querySelector<HTMLButtonElement>('[aria-label="Open session map"]')!.getBoundingClientRect()
        const viewport = document.querySelector('[data-slot="aui_thread-viewport"]')!.getBoundingClientRect()
        return button.bottom <= viewport.top &&
          !(button.left < message.right && button.right > message.left && button.top < message.bottom && button.bottom > message.top)
      })
      expect(clearOfMessage, 'session map launcher covers the first message').toBe(true)
      await launcher.click()
      await expect(page.getByRole('dialog', { name: 'Session map drawer' })).toBeVisible()
      await page.getByRole('dialog', { name: 'Session map drawer' }).getByRole('button', { name: 'Close session map' }).click()
      await expect(page.getByRole('dialog', { name: 'Session map drawer' })).toHaveCount(0)
    }
  }
})
