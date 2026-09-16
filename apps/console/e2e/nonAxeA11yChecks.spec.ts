import { test, expect } from '@playwright/test'
import { gotoRoute, assertShellMounted } from './helpers'
import { MOUSE_ONLY, CLIPPED_RING } from './nonAxeA11yChecks'

test('the non-axe detectors still recognise the defects they exist for', async ({ page }) => {
  await gotoRoute(page, 'dashboard')
  await assertShellMounted(page)

  const baselineMouse = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  const baselineRing = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(baselineMouse, 'dashboard must start clean for this probe to mean anything').toEqual([])
  expect(baselineRing, 'dashboard must start clean for this probe to mean anything').toEqual([])

  await page.evaluate(`(() => {
    const host = document.createElement('div')
    host.id = 'non-axe-probe'
    // 1. a mouse-only click target: pointer cursor, no tag/role/tabindex, wraps no control
    const mouseOnly = document.createElement('div')
    mouseOnly.style.cssText = 'cursor:pointer;width:120px;height:32px'
    mouseOnly.textContent = 'probe-mouse-only'
    // 2. a focusable button whose OUTWARD ring is clipped by an overflow-hidden parent it fills
    const clip = document.createElement('div')
    clip.style.cssText = 'overflow:hidden;width:120px;height:32px'
    const btn = document.createElement('button')
    btn.style.cssText = 'width:120px;height:32px;outline:2px solid red;outline-offset:0'
    btn.textContent = 'probe-clipped'
    clip.appendChild(btn)
    host.append(mouseOnly, clip)
    document.body.appendChild(host)
  })()`)

  const mouseOnly = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  expect(
    mouseOnly.join('\n'),
    'the mouse-only detector no longer finds a pointer-cursor div with no keyboard path',
  ).toContain('probe-mouse-only')

  const clipped = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(
    clipped.join('\n'),
    'the clipped-ring detector no longer finds an outward ring inside an overflow-hidden parent',
  ).toContain('probe-clipped')

  await page.evaluate(`(() => {
    const b = document.querySelector('#non-axe-probe button')
    if (b) b.style.outlineOffset = '-2px'
  })()`)
  const afterFix = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(
    afterFix.join('\n'),
    'an inset ring is the prescribed fix and must NOT be reported',
  ).not.toContain('probe-clipped')

  await page.evaluate(`(() => { document.getElementById('non-axe-probe')?.remove() })()`)
})

test('the app-host shell this manifest scans really does render', async ({ page }) => {
  await gotoRoute(page, 'app/shell/not-a-real-app')

  await expect(
    page.getByText(/isn’t installed/),
    'the app-host 404 EmptyState must be what a scan of #/app/<not-installed> sees',
  ).toBeVisible()

  const cta = page.getByRole('button', { name: /Open the Store/i })
  await expect(cta, 'the EmptyState action must be a real control').toBeVisible()
  await cta.focus()
  await expect(cta, 'and it must be focusable, or there is nothing here for these checks to sweep')
    .toBeFocused()
})
