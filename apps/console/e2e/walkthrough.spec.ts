import { test, expect } from '@playwright/test'
import { ROUTES, SETTINGS_ROUTES, VIEW_ROUTES, NON_NAV_ROUTES, THEMES, type Theme } from './routes'
import { seedTheme, gotoRoute, assertMounted, OPENERS } from './helpers'


const TAB_CAP = 60

const SURFACES = [...ROUTES, ...SETTINGS_ROUTES, ...VIEW_ROUTES, ...NON_NAV_ROUTES]

const KNOWN_UNANNOUNCED: { route?: string; opener?: string; match: string; why: string }[] = [
  {
    opener: 'command palette',
    match: 'Search pages and actions',
    why: "ui/SearchField's inline variant, deliberately unringed — see SearchField.tsx around the OVERLAY_FOCUS constant.",
  },
]

async function seedThemeAndMedia(page: import('@playwright/test').Page, theme: Theme, reduce: boolean) {
  await seedTheme(page, theme)
  await page.emulateMedia({
    colorScheme: theme,
    reducedMotion: reduce ? 'reduce' : 'no-preference',
  })
}

async function installProbes(page: import('@playwright/test').Page) {
  await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>
    const effective = (cs: CSSStyleDeclaration) => {
      const w = parseFloat(cs.outlineWidth) || 0
      const outline = cs.outlineStyle !== 'none' && w > 0
        ? `o:${w}:${cs.outlineStyle}:${cs.outlineColor}` : ''
      const shadow = cs.boxShadow && cs.boxShadow !== 'none' ? `s:${cs.boxShadow}` : ''
      return [outline, shadow, `bd:${cs.borderColor}`, `bg:${cs.backgroundColor}`,
              `fg:${cs.color}`, `op:${cs.opacity}`].join('~')
    }
    w.__sig = (el: Element | null) => {
      if (!el) return 'none'
      const parts: string[] = []
      let cur: Element | null = el
      for (let i = 0; i < 4 && cur; i++) {
        parts.push(effective(getComputedStyle(cur)))
        cur = cur.parentElement
      }
      const kids = Array.from(el.querySelectorAll('*')).slice(0, 25)
      for (const k of kids) parts.push(effective(getComputedStyle(k)))
      return parts.join('||')
    }
    w.__desc = (el: Element | null) => {
      if (!el) return 'none'
      const e = el as HTMLElement
      const cls = typeof e.className === 'string' ? e.className.split(/\s+/).slice(0, 3).join('.') : ''
      const name = (e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 32)
      return `<${el.tagName.toLowerCase()}${cls ? ' .' + cls : ''}>${name ? ` "${name}"` : ''}`
    }
    w.__caretCredited = (el: Element | null) => {
      if (!el) return false
      const e = el as HTMLElement
      if (e.isContentEditable !== true) return false
      const cs = getComputedStyle(e)
      return cs.caretColor !== 'transparent'
    }
  })

}

async function tabWalk(page: import('@playwright/test').Page) {
  const seen = new Set<string>()
  const unannounced: string[] = []
  let presses = 0
  let cappedOut = false

  for (; presses < TAB_CAP; presses++) {
    await page.keyboard.press('Tab')
    const r = await page.evaluate(async () => {
      const el = document.activeElement
      if (!el || el === document.body) return null
      const w = window as unknown as Record<string, (el: Element | null) => unknown>
      const desc = w.__desc(el) as string
      if (w.__caretCredited(el)) return { desc, ok: true, credited: true }
      const sig = () => w.__sig(el) as string
      const settle = () => new Promise((r) => setTimeout(r, 260))

      const focused = sig()
      ;(el as HTMLElement).blur()
      const resting = sig()
      if (focused !== resting) {
        ;(el as HTMLElement).focus()
        return { desc, ok: true, credited: false }
      }
      ;(el as HTMLElement).focus()
      await settle()
      const focusedSettled = sig()
      ;(el as HTMLElement).blur()
      await settle()
      const restingSettled = sig()
      ;(el as HTMLElement).focus()
      return { desc, ok: focusedSettled !== restingSettled, credited: false }
    })
    if (!r) break
    if (seen.has(r.desc)) break
    seen.add(r.desc)
    if (!r.ok) unannounced.push(r.desc)
  }
  cappedOut = presses >= TAB_CAP
  return { stops: seen.size, presses, capped: cappedOut, unannounced }
}


for (const theme of THEMES) {
  test.describe(`V3 keyboard-only: ${theme} theme`, () => {
    for (const { route, label } of SURFACES) {
      test(`${label} (#/${route}) — every focus stop announces itself`, async ({ page }, testInfo) => {
        await seedThemeAndMedia(page, theme, false)
        await gotoRoute(page, route)

        await installProbes(page)
        const walk = await tabWalk(page)
        const { unannounced } = walk
        const seenSize = walk.stops
        const presses = walk.presses
        const cappedOut = walk.capped

        await testInfo.attach(`kbd-${route}-${theme}.txt`, {
          body: `stops=${seenSize} presses=${presses} cappedAtTabCap=${cappedOut}\n` +
            `unannounced=${unannounced.length}\n${unannounced.map((u) => '  ' + u).join('\n')}`,
          contentType: 'text/plain',
        })

        expect(
          seenSize,
          `the Tab-walk reached NO focusable element on #/${route} — the walk broke, it did not ` +
            `find a clean route. (Every route in routes.ts is shell-bearing, and the shell alone ` +
            `carries the nav rail's stops.)`,
        ).toBeGreaterThan(0)

        const allowed = KNOWN_UNANNOUNCED.filter((k) => k.route === route)
        const unexpected = unannounced.filter((u) => !allowed.some((k) => u.includes(k.match)))

        expect(
          unexpected,
          `${unexpected.length} keyboard focus stop(s) on #/${route} (${theme}) change NOTHING ` +
            `visible when focused — not on the element, not on its three nearest ancestors, not on ` +
            `a descendant. The user cannot see where they are. Add a focus treatment (focus:ring-2 ` +
            `on the control, focus-within: on the box-drawing container, or group-focus-visible: on ` +
            `the child that paints the seam), or drop the outline-none and let tokens.css's global ` +
            `ring paint:\n${unexpected.map((u) => '  ' + u).join('\n')}`,
        ).toEqual([])

        for (const k of allowed) {
          expect(
            unannounced.some((u) => u.includes(k.match)),
            `KNOWN_UNANNOUNCED names "${k.match}" on #/${route}, but that stop now announces its ` +
              `focus. The gate is wider than the code needs: DELETE the entry.\n  ${k.why}`,
          ).toBe(true)
        }
      })
    }
    for (const opener of OPENERS) {
      test(`${opener.label} [opened] — every focus stop announces itself`, async ({ page }, testInfo) => {
        await seedThemeAndMedia(page, theme, false)
        await gotoRoute(page, opener.route)

        const before = await page.evaluate(() => document.querySelectorAll('*').length)
        const opened = await opener.open(page)
        test.skip(opened !== true, opened === true ? '' : `${opener.label}: ${opener.skip}`)
        await page.waitForTimeout(700)
        await assertMounted(page, before, opener.label)
        await installProbes(page)

        const walk = await tabWalk(page)
        await testInfo.attach(`kbd-opened-${opener.label.replace(/\W+/g, '-')}-${theme}.txt`, {
          body: `stops=${walk.stops} presses=${walk.presses} cappedAtTabCap=${walk.capped}\n` +
            `unannounced=${walk.unannounced.length}\n${walk.unannounced.map((u) => '  ' + u).join('\n')}`,
          contentType: 'text/plain',
        })

        expect(
          walk.stops,
          `the Tab-walk reached NO focusable element on ${opener.label} — the walk broke, it did ` +
            `not find a clean surface.`,
        ).toBeGreaterThan(0)

        const allowedHere = KNOWN_UNANNOUNCED.filter((k) => k.opener === opener.label)
        const unexpectedHere = walk.unannounced.filter(
          (u) => !allowedHere.some((k) => u.includes(k.match)),
        )

        expect(
          unexpectedHere,
          `${unexpectedHere.length} keyboard focus stop(s) on ${opener.label} (${theme}) change ` +
            `NOTHING visible when focused — a surface the route-level walk never reaches:\n` +
            unexpectedHere.map((u) => '  ' + u).join('\n'),
        ).toEqual([])

        for (const k of allowedHere) {
          expect(
            walk.unannounced.some((u) => u.includes(k.match)),
            `KNOWN_UNANNOUNCED names "${k.match}" on ${opener.label}, but that stop now announces ` +
              `its focus. DELETE the entry.\n  ${k.why}`,
          ).toBe(true)
        }
      })
    }
  })
}


for (const theme of THEMES) {
  test.describe(`V3 reduced-motion: ${theme} theme`, () => {
    test.use({ reducedMotion: 'reduce' })
    for (const { route, label } of SURFACES) {
      test(`${label} (#/${route}) — motion is suppressed`, async ({ page }, testInfo) => {
        await seedThemeAndMedia(page, theme, true)
        await gotoRoute(page, route)
        await page.waitForTimeout(1_000)

        const m = await page.evaluate(() => {
          const long: string[] = []
          let total = 0
          for (const el of document.querySelectorAll('*')) {
            total++
            const cs = getComputedStyle(el)
            const td = Math.max(...cs.transitionDuration.split(',').map((s) => parseFloat(s) || 0))
            const ad = Math.max(...cs.animationDuration.split(',').map((s) => parseFloat(s) || 0))
            const animated = ad > 0.05 && cs.animationName !== 'none'
            if (td > 0.05 || animated) {
              const e = el as HTMLElement
              const cls = typeof e.className === 'string' ? e.className.split(/\s+/).slice(0, 3).join('.') : ''
              long.push(`<${el.tagName.toLowerCase()}${cls ? ' .' + cls : ''}> ` +
                        `transition=${td}s animation=${animated ? cs.animationName + ' ' + ad + 's' : 'none'}`)
            }
          }
          return {
            mq: matchMedia('(prefers-reduced-motion: reduce)').matches,
            long: long.slice(0, 12),
            longCount: long.length,
            total,
          }
        })

        await testInfo.attach(`reduce-${route}-${theme}.txt`, {
          body: `mqReduce=${m.mq} elementsWithLongMotion=${m.longCount} of ${m.total}\n` +
            m.long.map((l) => '  ' + l).join('\n'),
          contentType: 'text/plain',
        })

        expect(
          m.mq,
          `prefers-reduced-motion is NOT emulated on this page, so this leg is measuring a user ` +
            `with no preference. seedTheme's emulateMedia({colorScheme}) clears it — re-apply it.`,
        ).toBe(true)

        expect(
          m.longCount,
          `${m.longCount} element(s) on #/${route} (${theme}) still declare motion over 50ms with ` +
            `prefers-reduced-motion: reduce set. Measured on main: 55-102 per route WITHOUT the ` +
            `preference and 0 WITH it, so the global suppression works and a non-zero count here ` +
            `means a new rule escaped it:\n${m.long.map((l) => '  ' + l).join('\n')}`,
        ).toBe(0)
      })
    }
  })
}


for (const theme of THEMES) {
  test.describe(`V3 phone viewport: ${theme} theme`, () => {
    test.use({ viewport: { width: 390, height: 844 } })
    for (const { route, label } of SURFACES) {
      test(`${label} (#/${route}) — no horizontal overflow at 390px`, async ({ page }, testInfo) => {
        await seedThemeAndMedia(page, theme, false)
        await gotoRoute(page, route)

        const m = await page.evaluate(() => {
          const vw = window.innerWidth
          const offscreen: string[] = []
          let inScroller = 0
          const inHorizontalScroller = (el: Element): boolean => {
            let cur: Element | null = el.parentElement
            while (cur && cur !== document.documentElement) {
              const ox = getComputedStyle(cur).overflowX
              if (ox === 'auto' || ox === 'scroll') return true
              cur = cur.parentElement
            }
            return false
          }
          for (const el of document.querySelectorAll('*')) {
            const r = el.getBoundingClientRect()
            if (r.width === 0 || r.height === 0) continue
            if (getComputedStyle(el).visibility !== 'visible') continue
            if (r.right > vw + 1) {
              if (inHorizontalScroller(el)) {
                inScroller++
                continue
              }
              const e = el as HTMLElement
              const cls = typeof e.className === 'string' ? e.className.split(/\s+/).slice(0, 3).join('.') : ''
              offscreen.push(`<${el.tagName.toLowerCase()}${cls ? ' .' + cls : ''}> right=${Math.round(r.right)}px`)
            }
          }
          return {
            vw,
            scrollW: document.documentElement.scrollWidth,
            clientW: document.documentElement.clientWidth,
            offscreen: offscreen.slice(0, 10),
            offscreenCount: offscreen.length,
            inScroller,
          }
        })

        await testInfo.attach(`phone-${route}-${theme}.txt`, {
          body: `vw=${m.vw} scrollW=${m.scrollW} clientW=${m.clientW} offscreenRight=${m.offscreenCount} ` +
            `(creditedInsideAHorizontalScroller=${m.inScroller})\n` +
            m.offscreen.map((o) => '  ' + o).join('\n'),
          contentType: 'text/plain',
        })

        expect(m.vw, 'the phone viewport did not apply — this ran at desktop width').toBe(390)

        expect(
          m.scrollW,
          `#/${route} (${theme}) scrolls horizontally at 390px (scrollWidth ${m.scrollW} > ` +
            `clientWidth ${m.clientW}). A phone user has to pan sideways to read the page.`,
        ).toBeLessThanOrEqual(m.clientW + 1)

        expect(
          m.offscreen,
          `${m.offscreenCount} element(s) extend past the right edge at 390px on #/${route} ` +
            `(${theme}). Nothing overflowed on any route measured on main, so this is new:\n` +
            m.offscreen.map((o) => '  ' + o).join('\n'),
        ).toEqual([])
      })
    }
  })
}
