import { test } from '@playwright/test'
import { ROUTES, VIEW_ROUTES, THEMES } from './routes'
import { seedTheme, gotoRoute, expectRouteScreenshot } from './helpers'

const VISUAL_ROUTES = [...ROUTES, ...VIEW_ROUTES]

test.describe.configure({ mode: 'serial' })
test.use({ reducedMotion: 'reduce' })

for (const theme of THEMES) {
  test.describe(`visual: ${theme} theme`, () => {
    for (const { route, id, label } of VISUAL_ROUTES) {
      test(`${label} (#/${route})`, async ({ page }) => {
        await seedTheme(page, theme)
        await gotoRoute(page, route)
        await expectRouteScreenshot(page, `${id ?? route}-${theme}`)
      })
    }
  })
}
