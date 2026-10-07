import { describe, expect, it, vi, afterEach } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { createElement } from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { api } from '../data/api'
import { DegradedChip } from './DegradedChip'
import { useShellNavigation } from '../../app/shell/shellControllers'



const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

describe('DegradedChip is dismissible from the keyboard', () => {
  const src = read('shared/ui/DegradedChip.tsx')

  it('Escape closes it and returns focus to the chip', () => {
    expect(src).toMatch(/useDismissKey\('Escape', restore, 100\)/)
    expect(src).toMatch(/setOpen\(false\)/)
    expect(src).toMatch(/close\(\); trigger\.current\?\.focus\(\)/)
  })

  it('the trigger carries the ref that focus returns to', () => {
    expect(src).toMatch(/<button ref=\{triggerRef\}/)
  })

  it('Escape is consumed so one press does not close two layers', () => {
    const helper = read('shared/ui/overlayInteraction.ts')
    expect(helper).toMatch(/event\.preventDefault\(\)/)
    expect(helper).toMatch(/event\.stopImmediatePropagation\(\)/)
  })

  it('the listener is scoped to the open state', () => {
    expect(src).toMatch(/\{open && <DegradedPanel/)
  })
})

describe('the NavRail overlay drawer is dismissible from the keyboard', () => {
  const src = read('app/shell/App.tsx')

  it('Escape closes the drawer', () => {
    expect(src).toMatch(/const rail = useShellNavigation\(isMobile, navigate\)/)
    expect(src).toMatch(/onScrimClick=\{\(\) => rail\.close\(\)\}/)
    const controller = read('app/shell/shellControllers.ts')
    expect(controller).toMatch(/useDismissKey\('Escape', \(\) => dispatch\('close'\), 50, state\.open\)/)
    expect(read('shared/ui/NavRail.tsx')).toMatch(/new FocusScope\(captureFocus\(\)\)\.attach\(overlayRef\.current\)/)
  })

  it('the drawer is reachable at desktop widths, which is why it needs Escape', () => {
    expect(read('app/shell/useIsMobile.ts')).toMatch(/max-width: 768px/)
    expect(/navigator\.maxTouchPoints|ontouchstart/.test(read('app/shell/useIsMobile.ts'))).toBe(false)
  })
})

describe('the rail: an overlay with a click-away scrim also binds Escape', () => {
  const files = walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

  const SCRIM = /className="[^"]*\b(?:fixed|absolute)\b[^"]*\binset-0\b[^"]*"[^>]{0,140}onClick=/
  const withScrim = files.filter((f) => SCRIM.test(f.src))

  it('every file with a click-away scrim handles Escape', () => {
    const delegates: Record<string, [RegExp, string]> = {
      'shared/ui/content/ContentSurface.tsx': [/useContentTools\(draft\)/, 'shared/ui/content/contentSurfaceState.ts'],
      'shared/ui/widget/ReactWidgetFrame.tsx': [/useWidgetExpansion\(\)/, 'shared/ui/widget/widgetFrameState.ts'],
      'shared/ui/widget/WidgetFrame.tsx': [/useWidgetExpansion\(\)/, 'shared/ui/widget/widgetFrameState.ts'],
    }
    const handlesEscape = (source: string) => /['"]Escape['"]/.test(source)
    const offenders = withScrim.filter(f => {
      if (handlesEscape(f.src)) return false
      const delegate = delegates[f.rel]
      return !delegate || !delegate[0].test(f.src) || !handlesEscape(read(delegate[1]))
    }).map(f => f.rel)
    expect(
      offenders,
      `A scrim is a MOUSE dismissal; without an Escape handler a keyboard user cannot close the ` +
        `overlay:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the scrim-bearing files', () => {
    expect(withScrim.length, 'the scanner must find the scrim-bearing overlays').toBeGreaterThanOrEqual(10)
    const rels = withScrim.map((f) => f.rel)
    expect(rels).toContain('shared/ui/DegradedChip.tsx')
    expect(rels).toContain('shared/ui/Modal.tsx')
    for (const cls of ['fixed inset-0 z-40', 'absolute inset-0 bg-canvas/70']) {
      expect(SCRIM.test(`<div className="${cls}" onClick={close} />`), cls).toBe(true)
    }
    expect(SCRIM.test('<div className="fixed inset-0 z-[100] overflow-hidden" style={{}}>')).toBe(false)
    const sample = { rel: 'x.tsx', src: '<div className="fixed inset-0 z-40" onClick={close} />' }
    expect(SCRIM.test(sample.src) && !/'Escape'/.test(sample.src)).toBe(true)
  })
})


afterEach(() => vi.restoreAllMocks())
function NativeLayers() {
  const rail = useShellNavigation(true, () => {})
  return createElement('div', null,
    createElement('button', { onClick: rail.toggle }, 'Open navigation'),
    rail.open && createElement('div', { 'data-testid': 'navigation-layer' }, 'Navigation open'),
    createElement(DegradedChip),
  )
}
describe('native status over navigation Escape layering', () => {
  it('closes only the status layer, restores focus, then closes the rail and removes its listener', async () => {
    vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: [{ surface: 'search_ranking', available: false, floor: 'Keyword ranking', backlog: 0, use_cases: ['embedding'] }], degraded: ['search_ranking'] })
    vi.spyOn(api, 'onboarding').mockRejectedValue(new Error('not needed for this diagnosis'))
    render(createElement(NativeLayers))
    fireEvent.click(screen.getByRole('button', { name: 'Open navigation' }))
    expect(screen.getByTestId('navigation-layer')).toBeTruthy()
    const trigger = await screen.findByRole('button', { name: /degraded/i })
    trigger.focus(); fireEvent.click(trigger)
    await screen.findByRole('dialog')
    const first = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    fireEvent(document, first)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(first.defaultPrevented).toBe(true)
    expect(document.activeElement).toBe(trigger)
    expect(screen.getByTestId('navigation-layer')).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByTestId('navigation-layer')).toBeNull())
    const idle = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    fireEvent(document, idle)
    expect(idle.defaultPrevented).toBe(false)
  })
})
