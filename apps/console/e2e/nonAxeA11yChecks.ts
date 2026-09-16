import { expect, type Page } from '@playwright/test'


export const MOUSE_ONLY = `() => {
  const NATIVE = new Set(['A','BUTTON','INPUT','SELECT','TEXTAREA','SUMMARY','OPTION','LABEL'])
  const ROLES = new Set(['button','link','menuitem','menuitemcheckbox','menuitemradio','tab','option',
    'checkbox','radio','switch','treeitem','gridcell','combobox','slider'])
  const interactive = (el) => {
    if (!el || el === document.body) return false
    if (NATIVE.has(el.tagName)) return true
    const r = el.getAttribute('role')
    if (r && ROLES.has(r)) return true
    return el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1'
  }
  const anyInteractiveAncestor = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) if (interactive(p)) return true
    return false
  }
  const wrapsControl = (el) => !!el.querySelector('a[href],button,input,select,textarea,[role=button],[role=link],[tabindex]:not([tabindex="-1"])')
  const path = (el) => {
    const bits = []
    for (let e = el; e && e !== document.body && bits.length < 3; e = e.parentElement) {
      const cls = (e.className || '').toString().split(/\\s+/).filter(Boolean).slice(0, 2).join('.')
      bits.unshift(e.tagName.toLowerCase() + (cls ? '.' + cls : ''))
    }
    return bits.join('>')
  }
  const out = []
  for (const el of document.querySelectorAll('*')) {
    if (interactive(el)) continue
    if (getComputedStyle(el).cursor !== 'pointer') continue
    const p = el.parentElement
    if (p && p !== document.body && getComputedStyle(p).cursor === 'pointer') continue
    if (anyInteractiveAncestor(el)) continue
    const r = el.getBoundingClientRect()
    if (r.width < 24 || r.height < 16) continue
    if (wrapsControl(el)) continue
    out.push(path(el) + '  "' + ((el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 40)) + '"')
  }
  return [...new Set(out)]
}`

export const CLIPPED_RING = `() => {
  const els = [...document.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),[tabindex]:not([tabindex="-1"])')]
  const path = (el) => {
    const bits = []
    for (let e = el; e && e !== document.body && bits.length < 3; e = e.parentElement) {
      const cls = (e.className || '').toString().split(/\\s+/).filter(Boolean).slice(0, 2).join('.')
      bits.unshift(e.tagName.toLowerCase() + (cls ? '.' + cls : ''))
    }
    return bits.join('>')
  }
  const restore = document.activeElement
  const out = []
  for (const el of els) {
    const r = el.getBoundingClientRect()
    if (r.width < 8 || r.height < 8) continue
    try { el.focus({ preventScroll: true }) } catch (e) { continue }
    const cs = getComputedStyle(el)
    const w = parseFloat(cs.outlineWidth) || 0
    const off = parseFloat(cs.outlineOffset) || 0
    if (!w || cs.outlineStyle === 'none') continue
    if (off < 0) continue
    let cl = null
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      // The APP SHELL is not a clipper for this purpose. \`html, body, #root { overflow: hidden }\` is
      // this app's layout, so the shell clips at the VIEWPORT EDGE -- which is a fact about where the
      // fold lands, not a defect in any component, and nothing a component could fix.
      if (p.id === 'root' || p === document.body) continue
      const c = getComputedStyle(p)
      if (['hidden','clip'].includes(c.overflowX) || ['hidden','clip'].includes(c.overflowY)) { cl = p; break }
    }
    if (!cl) continue
    const cr = cl.getBoundingClientRect()
    const insideClipper = r.top >= cr.top - 1 && r.left >= cr.left - 1 && r.right <= cr.right + 1 && r.bottom <= cr.bottom + 1
    if (!insideClipper) continue
    const need = w + off
    const lost = {
      top: cr.top - (r.top - need), left: cr.left - (r.left - need),
      right: (r.right + need) - cr.right, bottom: (r.bottom + need) - cr.bottom,
    }
    const bad = Object.entries(lost).filter(([, v]) => v > 0.5 && v <= 6)
    if (!bad.length) continue
    // The CLIPPER is named too, because that is often where the fix belongs. The first version
    // reported only the focused control, which sent me to a shared primitive (ui/IconButton, ~200
    // call sites) for a defect that existed in ONE container -- the wrong blast radius by two orders
    // of magnitude. Whether to inset the ring or fix the container needs both ends named.
    // NB: no backticks in this comment. It lives INSIDE a template string, and a backtick here ends
    // the literal -- which is exactly how it broke on the first run.
    out.push(path(el) + '  "' + ((el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30))
      + '"  ring ' + w + 'px offset ' + off + 'px clipped ' + bad.map(([k, v]) => k + ':' + v.toFixed(1)).join(' ')
      + '  clipped-by ' + path(cl) + (cl.className ? ' [' + cl.className.toString().slice(0, 60) + ']' : ''))
  }
  if (restore instanceof HTMLElement) { try { restore.focus({ preventScroll: true }) } catch (e) { /* ignore */ } }
  return [...new Set(out)]
}`


export async function expectNoNonAxeA11yDefects(page: Page, where: string): Promise<void> {
  const mouseOnly = await page.evaluate<string[]>(`(${MOUSE_ONLY})()`)
  expect(
    mouseOnly,
    `${where}: element promises a click (cursor: pointer) with no tag, role or tab stop, and wraps no `
    + `control — mouse-only (WCAG 2.1.1). Render a real <button>, or drop the pointer cursor:\n  `
    + mouseOnly.join('\n  '),
  ).toEqual([])

  const clipped = await page.evaluate<string[]>(`(${CLIPPED_RING})()`)
  expect(
    clipped,
    `${where}: a focused control's outline falls outside a clipping ancestor, so the ring computes but `
    + `never paints (WCAG 2.4.7). Use a negative outline-offset — and match the parent's radius, or `
    + `the ring's corners clip instead:\n  ` + clipped.join('\n  '),
  ).toEqual([])
}
