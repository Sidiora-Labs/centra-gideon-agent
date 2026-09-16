import { test, expect } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'
import { ROUTES, SETTINGS_ROUTES, VIEW_ROUTES, NON_NAV_ROUTES, THEMES } from './routes'
import { seedTheme, gotoRoute, assertMounted, OPENERS } from './helpers'
import { expectNoNonAxeA11yDefects } from './nonAxeA11yChecks'


const BLOCKING = new Set(['serious', 'critical'])


for (const theme of THEMES) {
  test.describe(`a11y (WCAG AA): ${theme} theme`, () => {
    for (const { route, id, label } of [
      ...ROUTES,
      ...SETTINGS_ROUTES,
      ...VIEW_ROUTES,
      ...NON_NAV_ROUTES,
    ]) {
      test(`${label} (#/${route})`, async ({ page }, testInfo) => {
        await seedTheme(page, theme)
        await gotoRoute(page, route)

        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze()

        const blocking = results.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
        await testInfo.attach(`axe-${id ?? route}-${theme}.json`, {
          body: JSON.stringify(results.violations, null, 2),
          contentType: 'application/json',
        })

        expect(
          blocking,
          `serious/critical a11y violations on #/${route} (${theme}):\n` +
            blocking.map((v) => `  [${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s)`).join('\n'),
        ).toEqual([])

        await expectNoNonAxeA11yDefects(page, `#/${route} (${theme})`)
      })
    }

    for (const opener of OPENERS) {
      test(`${opener.label} [opened]`, async ({ page }, testInfo) => {
        await seedTheme(page, theme)
        await gotoRoute(page, opener.route)

        const before = await page.evaluate(() => document.querySelectorAll('*').length)
        const opened = await opener.open(page)
        test.skip(opened !== true, opened === true ? '' : `${opener.label}: ${opened.skip}`)
        await page.waitForTimeout(700)
        await assertMounted(page, before, opener.label)

        const results = await new AxeBuilder({ page })
          .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
          .analyze()

        const blocking = results.violations.filter((v) => BLOCKING.has(v.impact ?? ''))
        await testInfo.attach(`axe-${opener.label.replace(/\W+/g, '-')}-${theme}.json`, {
          body: JSON.stringify(results.violations, null, 2),
          contentType: 'application/json',
        })

        expect(
          blocking,
          `serious/critical a11y violations on ${opener.label} (${theme}) — a surface the\n` +
            `route-level scan never reaches:\n` +
            blocking.map((v) => `  [${v.impact}] ${v.id}: ${v.help} — ${v.nodes.length} node(s)`).join('\n'),
        ).toEqual([])

        await expectNoNonAxeA11yDefects(page, `${opener.label} [opened] (${theme})`)
      })
    }
  })
}
