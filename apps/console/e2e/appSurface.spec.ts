import { test, expect } from '@playwright/test'
import { gotoRoute, assertShellMounted } from './helpers'
import { MOUSE_ONLY, CLIPPED_RING } from './nonAxeA11yChecks'


const MARKER = 'e2e-app-surface'

test('the app-authored surface really mounts, and the detectors judge IT', async ({ page }) => {
  await gotoRoute(page, 'app/shell/e2e-ui-fixture')
  await assertShellMounted(page)

  const surface = page.locator(`[data-testid="${MARKER}"]`)
  await expect(surface, 'the app bundle did not mount — the sweep would be scanning host chrome')
    .toBeVisible()
  await expect(page.getByRole('heading', { name: 'App-authored surface' })).toBeVisible()

  const button = page.locator(`[data-testid="${MARKER}-button"]`)
  await expect(button).toBeVisible()
  await button.focus()
  await expect(button, 'the app-authored control cannot take focus').toBeFocused()

  await expect(page.getByText("isn’t installed")).toHaveCount(0)

  const mouseOnly = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  const clippedRing = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(mouseOnly, 'app-authored mouse-only click targets').toEqual([])
  expect(clippedRing, 'app-authored clipped focus rings').toEqual([])
})
