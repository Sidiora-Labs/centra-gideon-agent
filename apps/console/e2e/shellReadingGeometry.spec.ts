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

  for (const width of [2560, 1440, 769, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 1080 })
    await page.evaluate(() => document.documentElement.style.setProperty('--content-width', '100%'))
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))))
    const geometry = await page.evaluate(() => {
      const group = document.querySelector<HTMLElement>('[data-gideon-chat-content] [data-slot="aui_message-group"]')!
      const composer = document.querySelector<HTMLElement>('[data-tour="chat"]')!
      const assistant = group.querySelector<HTMLElement>('.gideon-chat-assistant')!
      const workspace = document.querySelector<HTMLElement>('.gideon-workspace')!
      const header = document.querySelector<HTMLElement>('[data-gideon-chat-page] > .gideon-topbar')
      const headerTitle = header?.querySelector<HTMLElement>('[data-header-left]')
      const newChat = header?.querySelector<HTMLElement>('[title="New chat"]')
      const workspaceAction = header?.querySelector<HTMLElement>('[title="Workspace"]')
      const briefAction = header?.querySelector<HTMLElement>('[title="Brief the agent"]')
      const overflow = header?.querySelector<HTMLElement>('[title="More actions"]')
      const labelFits = (action?: HTMLElement | null) => {
        const label = action?.querySelector('span')?.getBoundingClientRect()
        const clip = action?.closest('.overflow-x-auto')?.getBoundingClientRect()
        return !!label && !!clip && label.left >= clip.left - 1 && label.right <= clip.right + 1
      }
      const groupBox = group.getBoundingClientRect()
      const composerBox = composer.getBoundingClientRect()
      const assistantBox = assistant.getBoundingClientRect()
      const workspaceBox = workspace.getBoundingClientRect()
      const headerStyle = header ? getComputedStyle(header) : null
      return {
        groupWidth: groupBox.width,
        composerWidth: composerBox.width,
        centerDrift: Math.abs((groupBox.left + groupBox.right - composerBox.left - composerBox.right) / 2),
        assistantInset: assistantBox.left - workspaceBox.left,
        pageOverflow: document.documentElement.scrollWidth - innerWidth,
        headerWidth: header ? header.clientWidth - parseFloat(headerStyle!.paddingLeft) - parseFloat(headerStyle!.paddingRight) : null,
        titleWidth: headerTitle?.getBoundingClientRect().width ?? null,
        primaryReachable: !!newChat && newChat.getBoundingClientRect().width >= 40 && newChat.getBoundingClientRect().right <= innerWidth,
        primaryLabeled: newChat?.textContent?.includes('New chat') && workspaceAction?.textContent?.includes('Workspace') && labelFits(newChat) && labelFits(workspaceAction),
        secondaryLabeledOrOverflowed: !!overflow || !!briefAction?.textContent?.includes('Brief the agent'),
      }
    })
    expect(geometry.groupWidth).toBeLessThanOrEqual(821)
    expect(geometry.composerWidth).toBeLessThanOrEqual(821)
    expect(geometry.centerDrift).toBeLessThan(3)
    expect(geometry.pageOverflow).toBeLessThanOrEqual(1)
    if (width > 768) {
      if (width > 1100) expect(geometry.assistantInset).toBeGreaterThan(48)
      expect(geometry.headerWidth).not.toBeNull()
      expect(geometry.headerWidth!).toBeLessThanOrEqual(1122)
      expect(geometry.titleWidth).toBeGreaterThan(150)
      expect(geometry.primaryReachable).toBe(true)
      expect(geometry.primaryLabeled).toBe(true)
      expect(geometry.secondaryLabeledOrOverflowed).toBe(true)
    }
    await testInfo.attach(`conversation-${width}`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' })
  }
})
