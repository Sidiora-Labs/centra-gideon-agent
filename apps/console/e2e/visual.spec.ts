import { test } from '@playwright/test'
import { ROUTES, VIEW_ROUTES, THEMES } from './routes'
import { seedTheme, gotoRoute, expectRouteScreenshot } from './helpers'


for (const theme of THEMES) {
  test.describe(`visual: ${theme} theme`, () => {
    for (const { route, id, label } of [...ROUTES, ...VIEW_ROUTES]) {
      test(`${label} (#/${route})`, async ({ page }) => {
        await seedTheme(page, theme)
        await gotoRoute(page, route)
        await expectRouteScreenshot(page, `${id ?? route}-${theme}`)
      })
    }
  })
}
