import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { gotoRoute } from './helpers'
import { SCRIPTED } from '../playwright.config'


const PROMPT = 'Give me your scripted line, please.'

test.describe('scripted chat turn (PHF-7)', () => {
  test.describe.configure({ timeout: 120_000 })

  test('completes a turn on the scripted provider — no network, no credential', async ({ page }) => {
    const fixture = readFileSync(SCRIPTED.scriptPath, 'utf8')
    expect(
      fixture,
      `${SCRIPTED.scriptPath} no longer scripts SCRIPTED.reply (playwright.config.ts).\n` +
        `The fixture and the expected reply must agree — reconcile the SCRIPTED constant.`,
    ).toContain(SCRIPTED.reply)

    await gotoRoute(page, 'chat')

    const composer = page.getByRole('textbox', { name: 'Message input' })
    await expect(composer).toBeVisible({ timeout: 15_000 })

    await composer.click()
    await composer.pressSequentially(PROMPT, { delay: 5 })

    const send = page.getByRole('button', { name: 'Send message', exact: true })
    await expect(send).toBeVisible()
    await expect(
      send,
      'the composer refused the draft — send stayed aria-disabled, so no turn was ever started',
    ).not.toHaveAttribute('aria-disabled', 'true')
    await send.click()

    await expect(page.getByText(PROMPT, { exact: false }).first()).toBeVisible({ timeout: 15_000 })

    await expect(
      page.getByText(SCRIPTED.reply, { exact: false }).first(),
      `the scripted reply never rendered. Either the '${SCRIPTED.type}' provider is not bound\n` +
        `(check the gateway's stdout for a provider-resolution error), or one of\n` +
        `${SCRIPTED.scriptEnvVar} did not reach it, or the turn\n` +
        `errored. The transcript above shows what did arrive.`,
    ).toBeVisible({ timeout: 60_000 })

    await expect(
      page.getByRole('button', { name: 'Stop', exact: true }),
      'the composer is still showing Stop — the turn started but never finished streaming',
    ).toHaveCount(0, { timeout: 60_000 })
    await expect(page.getByRole('button', { name: 'Send message', exact: true })).toBeVisible({ timeout: 30_000 })

    await expect(
      page.getByRole('button', { name: 'Regenerate', exact: true }),
      'the newest assistant turn has no action row — ChatPage renders it only when\n' +
        'that turn has stopped streaming, so the transcript still considers the turn\n' +
        'in flight even though the composer went idle.',
    ).toBeVisible({ timeout: 30_000 })
  })
})

