import { expect, test } from '@playwright/test'
import { SCRIPTED } from '../playwright.config'
import { gotoRoute } from './helpers'

const PROMPT = 'Give a short neutral status update.'

test('conversation keeps one centered reading column across desktop and mobile', async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 2560, height: 1080 })
  await gotoRoute(page, 'chat')
  const editor = page.getByRole('textbox', { name: 'Message input' })
  await editor.fill(PROMPT)
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
  await expect(page.getByText(SCRIPTED.reply, { exact: false }).first()).toBeVisible({ timeout: 60_000 })

  for (const width of [2560, 1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 })
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
    const geometry = await page.evaluate(() => {
      const group = document.querySelector<HTMLElement>('[data-gideon-chat-content] [data-slot="aui_message-group"]')!
      const composer = document.querySelector<HTMLElement>('[data-tour="chat"]')!
      const assistant = group.querySelector<HTMLElement>('.gideon-chat-assistant')!
      const workspace = document.querySelector<HTMLElement>('.gideon-workspace')!
      const header = document.querySelector<HTMLElement>('[data-gideon-chat-page] > .gideon-topbar')
      const groupBox = group.getBoundingClientRect()
      const composerBox = composer.getBoundingClientRect()
      const assistantBox = assistant.getBoundingClientRect()
      const workspaceBox = workspace.getBoundingClientRect()
      const headerStyle = header ? getComputedStyle(header) : null
      return {
        groupWidth: groupBox.width,
        centerDrift: Math.abs((groupBox.left + groupBox.right - composerBox.left - composerBox.right) / 2),
        assistantInset: assistantBox.left - workspaceBox.left,
        pageOverflow: document.documentElement.scrollWidth - innerWidth,
        headerWidth: header ? header.clientWidth - parseFloat(headerStyle!.paddingLeft) - parseFloat(headerStyle!.paddingRight) : null,
      }
    })
    expect(geometry.groupWidth).toBeLessThanOrEqual(821)
    expect(geometry.centerDrift).toBeLessThan(3)
    expect(geometry.pageOverflow).toBeLessThanOrEqual(1)
    if (width > 768) {
      expect(geometry.assistantInset).toBeGreaterThan(48)
      expect(geometry.headerWidth).not.toBeNull()
      expect(geometry.headerWidth!).toBeLessThanOrEqual(1122)
    }
    await testInfo.attach(`conversation-${width}`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' })
  }
})
