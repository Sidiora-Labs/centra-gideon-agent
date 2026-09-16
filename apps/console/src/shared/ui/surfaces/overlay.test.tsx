import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { SurfaceOverlayDoc, SurfaceOverlayPayload } from '../../data/api'
import { api } from '../../data/api'
import { allComponents, getComponent } from '../genui/registry'
import { registerCoreGenUiComponents } from '../genui/components'
import { GenUiWidget } from '../genui/GenUiWidget'
import { LAYER_USER } from './layers'
import { SurfaceOverlay } from './SurfaceOverlay'
import {
  CODE_OVERLAY_COMPONENT,
  loadSurfaceOverlays,
  overlayComponentNames,
  overlayRefusalsFor,
  overlaysFor,
  resetSurfaceOverlays,
  validateOverlayBody,
} from './overlay'

registerCoreGenUiComponents()

const BODY = 'a = Callout(tone: "info", text: "hello")'

function doc(over: Partial<SurfaceOverlayDoc> = {}): SurfaceOverlayDoc {
  return { file: 'mine.json', surface: 'dashboard', title: 'Mine', body: BODY, define: [], ...over }
}

function payload(over: Partial<SurfaceOverlayPayload> = {}): SurfaceOverlayPayload {
  return { overlays: [], refusals: [], dir: '/tmp/home/surfaces', ...over }
}

let fetched = 0

function serve(p: SurfaceOverlayPayload) {
  vi.spyOn(api, 'surfaceOverlays').mockImplementation(async () => {
    fetched += 1
    return p
  })
}

beforeEach(() => {
  window.location.hash = '#/dashboard'
  fetched = 0
  resetSurfaceOverlays()
})

afterEach(() => {
  vi.restoreAllMocks()
  resetSurfaceOverlays()
})


describe('an accepted overlay', () => {
  it('loads, and its band renders the tree it declares', async () => {
    serve(payload({ overlays: [doc()] }))
    const { findByText } = render(<SurfaceOverlay surface="dashboard" />)
    expect(await findByText('hello')).toBeInTheDocument()
    expect(overlaysFor('dashboard')).toHaveLength(1)
    expect(overlayRefusalsFor('dashboard')).toHaveLength(0)
    expect(fetched).toBe(1)
  })

  it('renders NOTHING when the home has no overlays (byte-identical to today)', async () => {
    serve(payload())
    const { container } = render(<SurfaceOverlay surface="dashboard" />)
    await loadSurfaceOverlays()
    expect(container.querySelector('[data-testid="surface-overlay"]')).toBeNull()
  })

  it('does not render an overlay aimed at a DIFFERENT surface', async () => {
    serve(payload({ overlays: [doc({ surface: 'somewhere-else' })] }))
    await loadSurfaceOverlays()
    expect(overlaysFor('dashboard')).toHaveLength(0)
  })

  it('fetches once per session even when two bands mount', async () => {
    serve(payload({ overlays: [doc()] }))
    await Promise.all([loadSurfaceOverlays(), loadSurfaceOverlays()])
    await loadSurfaceOverlays()
    expect(fetched).toBe(1)
  })
})


describe('clause 2 — an unknown component name is refused at load', () => {
  it('refuses the whole overlay and NAMES the component', async () => {
    serve(payload({ overlays: [doc({ body: `${BODY}\nb = NoSuchThing(x: 1)` })] }))
    const { findByTestId } = render(<SurfaceOverlay surface="dashboard" />)
    const notice = await findByTestId('overlay-refusal')
    expect(notice.textContent).toContain('NoSuchThing')
    expect(notice.textContent).toContain('mine.json')
    expect(document.body.textContent).not.toContain('hello')
    expect(overlaysFor('dashboard')).toHaveLength(0)
    expect(overlayRefusalsFor('dashboard')[0].error.code).toBe(CODE_OVERLAY_COMPONENT)
  })

  it('🪤 the chat path DROPS the same line instead — the contrast is measured', () => {
    const { container } = render(
      <GenUiWidget content={`${BODY}\nb = NoSuchThing(x: 1)`} title="Chat" />,
    )
    expect(container.textContent).toContain('hello')
    expect(container.textContent).toContain('Unknown component "NoSuchThing"')
  })

  it('a surface-less (backend) refusal still surfaces, on the home surface', async () => {
    serve(
      payload({
        refusals: [
          {
            file: 'broken.json',
            error: {
              code: 'ERR_SURFACE_OVERLAY_INVALID',
              what: "'broken.json' not valid JSON",
              why: 'because',
              fix: 'Rewrite it.',
              suggestions: [],
            },
          },
        ],
      }),
    )
    const { findByTestId } = render(<SurfaceOverlay surface="dashboard" />)
    expect((await findByTestId('overlay-refusal')).textContent).toContain('broken.json')
  })
})


describe('clause 4 — props go through the host schema', () => {
  it('refuses an excess arg', async () => {
    serve(payload({ overlays: [doc({ body: 'a = Callout(text: "hi", nope: 1)' })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')[0].error.what).toContain('unknown arg')
  })

  it('refuses a missing required arg', async () => {
    serve(payload({ overlays: [doc({ body: 'a = Callout(tone: "info")' })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')[0].error.what).toContain('missing required arg')
  })

  it('accepts the same component WITH its declared args (the control leg)', async () => {
    serve(payload({ overlays: [doc({ body: 'a = Callout(text: "hi", tone: "info")' })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')).toHaveLength(0)
    expect(overlaysFor('dashboard')).toHaveLength(1)
  })

  it('validateOverlayBody is the one walk both legs use', () => {
    expect(validateOverlayBody(BODY)).toBe('')
    expect(validateOverlayBody('a = Nope()')).toContain('Unknown component "Nope"')
    expect(overlayComponentNames(`${BODY}\nb = Badge(text: "x")`)).toEqual(['Callout', 'Badge'])
  })
})


describe('clause 3 — a define may ADD a component name, never shadow one', () => {
  const define = [{ name: 'MyPanel', description: 'mine', body: BODY }]

  it('registers a composite at L2 and renders it from the overlay body', async () => {
    serve(payload({ overlays: [doc({ body: 'p = MyPanel()', define })] }))
    const { findByText } = render(<SurfaceOverlay surface="dashboard" />)
    expect(await findByText('hello')).toBeInTheDocument()
    const reg = getComponent('MyPanel')
    expect(reg?.layer).toBe(LAYER_USER)
    expect(reg?.source).toBe('overlay:mine.json')
  })

  it('refuses a composite that takes a CORE name, and leaves nothing registered', async () => {
    const before = allComponents().length
    serve(
      payload({
        overlays: [doc({ body: 'p = MyPanel()', define: [...define, { name: 'Table', description: '', body: BODY }] })],
      }),
    )
    await loadSurfaceOverlays()
    const refusal = overlayRefusalsFor('dashboard')[0]
    expect(refusal.error.what).toContain('Table')
    expect(refusal.error.fix).toContain('never take a core one')
    expect(getComponent('MyPanel')).toBeUndefined()
    expect(getComponent('Table')?.source).toBe('')
    expect(allComponents()).toHaveLength(before)
    expect(overlaysFor('dashboard')).toHaveLength(0)
  })

  it('a composite whose OWN body names an unknown component is refused', async () => {
    serve(payload({ overlays: [doc({ body: 'p = Bad()', define: [{ name: 'Bad', description: '', body: 'x = Nope()' }] })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')[0].error.what).toContain('composite "Bad"')
    expect(getComponent('Bad')).toBeUndefined()
  })

  it('a composite takes NO args, so passing one is refused (clause 1 has no substitution)', async () => {
    serve(payload({ overlays: [doc({ body: 'p = MyPanel(value: 1)', define })] }))
    await loadSurfaceOverlays()
    expect(overlayRefusalsFor('dashboard')[0].error.what).toContain('unknown arg')
  })

  it('two overlays cannot take the same composite name', async () => {
    serve(
      payload({
        overlays: [doc({ file: 'a.json', body: 'p = MyPanel()', define }), doc({ file: 'b.json', body: 'p = MyPanel()', define })],
      }),
    )
    await loadSurfaceOverlays()
    expect(overlaysFor('dashboard').map((o) => o.file)).toEqual(['a.json'])
    expect(overlayRefusalsFor('dashboard')[0].file).toBe('b.json')
  })
})


describe('clause 6 — safe mode forces maxLayer=0, so no overlay loads', () => {
  it('does not even FETCH, and the band renders nothing', async () => {
    window.location.hash = '#/dashboard?safe=1'
    serve(payload({ overlays: [doc()] }))
    const { container } = render(<SurfaceOverlay surface="dashboard" />)
    await act(async () => { await loadSurfaceOverlays() })
    expect(fetched).toBe(0)
    expect(container.querySelector('[data-testid="surface-overlay"]')).toBeNull()
    expect(overlaysFor('dashboard')).toHaveLength(0)
  })

  it('the SAME payload loads with safe mode off (the control leg)', async () => {
    serve(payload({ overlays: [doc()] }))
    await loadSurfaceOverlays()
    expect(fetched).toBe(1)
    expect(overlaysFor('dashboard')).toHaveLength(1)
  })
})


describe('the dashboard call site', () => {
  it('🪤 DashboardPage renders the band for the `dashboard` surface', () => {
    const src = readFileSync(join(process.cwd(), "src/features/dashboard/DashboardPage.tsx"), 'utf8')
    expect(src).toContain('<SurfaceOverlay surface="dashboard" />')
    expect(src).toContain("from '../../shared/ui/surfaces/SurfaceOverlay'")
  })

  it('🪤 the loader has no dynamic-code path', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/ui/surfaces/overlay.tsx"), 'utf8')
    expect(src).not.toMatch(/\beval\s*\(/)
    expect(src).not.toMatch(/new Function\b/)
    expect(src).not.toMatch(/import\s*\(/)
  })
})
