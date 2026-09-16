import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  LAYER_APP,
  LAYER_CORE,
  LAYER_USER,
  layerName,
  maxSurfaceLayer,
  safeMode,
  safeModeInUrl,
  setServerSafeSurfaces,
} from './layers'
import { LayerBoundary } from './LayerBoundary'
import {
  allComponents,
  componentLayer,
  getComponent,
  library,
  registerLayerComponent,
  removeComponentsFrom,
} from '../genui/registry'
import { registerCoreGenUiComponents } from '../genui/components'
import { GenUiWidget } from '../genui/GenUiWidget'
import { ContributedPage } from '../../../features/apps/ContributedPage'

vi.mock('../../data/api', () => ({ api: {} }))

registerCoreGenUiComponents()

const APP = 'acme'
const gauge = {
  name: 'AcmeGauge',
  group: 'Data' as const,
  description: 'An app-contributed gauge',
  args: [{ key: 'value', type: 'number' as const, required: true }],
  component: ({ args }: { args: Record<string, unknown> }) => <span>gauge:{String(args.value)}</span>,
}

beforeEach(() => {
  removeComponentsFrom(APP)
  removeComponentsFrom('other-app')
  window.location.hash = '#/dashboard'
})


describe('the layer ceiling', () => {
  it('is the user layer by default', () => {
    expect(maxSurfaceLayer()).toBe(LAYER_USER)
    expect(safeMode()).toBe(false)
  })

  it('reads safe=1 out of the HASH route, where this app keeps its query', () => {
    expect(safeModeInUrl('#/dashboard?safe=1')).toBe(true)
    expect(safeModeInUrl('#/dashboard?tab=x&safe=1')).toBe(true)
    expect(safeModeInUrl('#/dashboard?safe=true')).toBe(true)
    expect(safeModeInUrl('#/dashboard?safe=yes')).toBe(true)
  })

  it('does not read it out of a plain search string or an unrelated param', () => {
    expect(safeModeInUrl('#/dashboard')).toBe(false)
    expect(safeModeInUrl('#/dashboard?tab=safe')).toBe(false)
    expect(safeModeInUrl('#/dashboard?safe=0')).toBe(false)
    expect(safeModeInUrl('#/dashboard?safe=false')).toBe(false)
  })

  it('drops to pure L0 when the hash asks for it', () => {
    expect(maxSurfaceLayer('#/dashboard?safe=1')).toBe(LAYER_CORE)
  })

  it('names each layer for the refusal copy', () => {
    expect([layerName(LAYER_CORE), layerName(LAYER_APP), layerName(LAYER_USER)])
      .toEqual(['core', 'app', 'user'])
  })
})


describe('a layered registration', () => {
  it('may ADD a name (the control leg)', () => {
    expect(registerLayerComponent(gauge, { layer: LAYER_APP, source: APP })).toEqual({ ok: true })
    expect(getComponent('AcmeGauge')).toBeTruthy()
    expect(componentLayer('AcmeGauge')).toBe(LAYER_APP)
  })

  it('may NOT shadow a core name', () => {
    const refusal = registerLayerComponent({ ...gauge, name: 'Table' }, { layer: LAYER_APP, source: APP })
    expect(refusal).toMatchObject({ ok: false, code: 'shadows-core' })
    expect(componentLayer('Table')).toBe(LAYER_CORE)
    expect(getComponent('Table')!.source).toBe('')
  })

  it('may not take a name another app already owns', () => {
    registerLayerComponent(gauge, { layer: LAYER_APP, source: 'other-app' })
    const refusal = registerLayerComponent(gauge, { layer: LAYER_APP, source: APP })
    expect(refusal).toMatchObject({ ok: false, code: 'shadows-layer' })
    expect(getComponent('AcmeGauge')!.source).toBe('other-app')
  })

  it('lets the SAME source re-register its own component (a reload is not a collision)', () => {
    registerLayerComponent(gauge, { layer: LAYER_APP, source: APP })
    expect(registerLayerComponent(gauge, { layer: LAYER_APP, source: APP })).toEqual({ ok: true })
  })

  it('refuses a nameless or renderer-less registration', () => {
    expect(registerLayerComponent({ ...gauge, name: '' }, { layer: LAYER_APP, source: APP }))
      .toMatchObject({ ok: false, code: 'invalid' })
  })

  it('refuses to pose as the core layer', () => {
    expect(registerLayerComponent(gauge, { layer: LAYER_CORE, source: APP }))
      .toMatchObject({ ok: false, code: 'invalid' })
  })

  it('is refused wholesale in safe mode', () => {
    window.location.hash = '#/dashboard?safe=1'
    expect(registerLayerComponent(gauge, { layer: LAYER_APP, source: APP }))
      .toMatchObject({ ok: false, code: 'layer-disabled' })
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })
})


describe('an app component in generated UIs', () => {
  it('appears in the authoring prompt, attributed to the app', () => {
    registerLayerComponent(gauge, { layer: LAYER_APP, source: APP })
    const prompt = library.prompt()
    expect(prompt).toContain('AcmeGauge')
    expect(prompt).toContain(`[from the ${APP} app]`)
  })

  it('renders inside a widget the same way a core component does', () => {
    registerLayerComponent(gauge, { layer: LAYER_APP, source: APP })
    const { getByText, queryByRole } = render(
      <GenUiWidget content={'g = AcmeGauge(value: 42)'} title="App tree" />,
    )
    expect(getByText('gauge:42')).toBeInTheDocument()
    expect(queryByRole('alert')).toBeNull()
  })

  it('is gone from BOTH the registry and the prompt when the app is disabled', () => {
    registerLayerComponent(gauge, { layer: LAYER_APP, source: APP })
    expect(removeComponentsFrom(APP)).toBe(1)
    expect(getComponent('AcmeGauge')).toBeUndefined()
    expect(library.prompt()).not.toContain('AcmeGauge')
    expect(allComponents().some((c) => c.source === APP)).toBe(false)
  })

  it('removing one app leaves the other apps AND the core set alone', () => {
    registerLayerComponent(gauge, { layer: LAYER_APP, source: APP })
    registerLayerComponent({ ...gauge, name: 'OtherThing' }, { layer: LAYER_APP, source: 'other-app' })
    const coreCount = allComponents().filter((c) => c.layer === LAYER_CORE).length
    removeComponentsFrom(APP)
    expect(getComponent('OtherThing')).toBeTruthy()
    expect(allComponents().filter((c) => c.layer === LAYER_CORE).length).toBe(coreCount)
  })

  it('removes nothing for an empty source (the core set has source "")', () => {
    const before = allComponents().length
    expect(removeComponentsFrom('')).toBe(0)
    expect(allComponents().length).toBe(before)
  })
})


describe('the layer boundary', () => {
  const Boom = () => { throw new Error('app component exploded') }

  it('catches a throwing layer and names it', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { getByRole } = render(
      <LayerBoundary layer={LAYER_APP} what="acme">
        <Boom />
      </LayerBoundary>,
    )
    expect(getByRole('alert').textContent).toContain('acme')
    expect(getByRole('alert').textContent).toContain('app layer')
    spy.mockRestore()
  })

  it('keeps a broken APP component from taking down the widget around it', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    registerLayerComponent(
      { ...gauge, name: 'AcmeBoom', component: Boom as never },
      { layer: LAYER_APP, source: APP },
    )
    const { getByText, container } = render(
      <GenUiWidget
        content={'a = AcmeBoom(value: 1)\nb = Callout(tone: "info", text: "still here")'}
        title="Mixed tree"
      />,
    )
    expect(getByText('still here')).toBeInTheDocument()
    expect(container.textContent).toContain('AcmeBoom')
    spy.mockRestore()
  })

  it('boundaries a USER-layer (L2) component too, not only an app one', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    registerLayerComponent(
      { ...gauge, name: 'UserBoom', component: Boom as never },
      { layer: LAYER_USER, source: 'user-overlay' },
    )
    const { getByRole } = render(<GenUiWidget content={'u = UserBoom(value: 1)'} title="Overlay" />)
    expect(getByRole('alert').textContent).toContain('user layer')
    removeComponentsFrom('user-overlay')
    spy.mockRestore()
  })

  it('wraps ONLY the layers above core, so a real L0 crash stays loud', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/ui/genui/GenUiWidget.tsx"), 'utf8')
    expect(src).toContain('if (def.layer <= LAYER_CORE) return node')
    expect(src).toMatch(/<LayerBoundary[^>]*layer=\{def\.layer\}/)
  })
})


describe('the app-page load site', () => {
  it('refuses to fetch a contributed bundle in safe mode, and says why', async () => {
    window.location.hash = '#/dashboard?safe=1'
    const { findByText } = render(
      <ContributedPage app={{ name: 'acme', permissions: {} }} src="/apps/acme/ui/index.mjs" />,
    )
    expect(await findByText(/Safe mode is on/)).toBeInTheDocument()
  })

  it('does not refuse when safe mode is off (the control leg)', async () => {
    const { findByText } = render(
      <ContributedPage app={{ name: 'acme', permissions: {} }} src="/apps/acme/ui/index.mjs" />,
    )
    const failure = await findByText(/Failed to load acme/)
    expect(failure).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('Safe mode is on')
  })
})


describe('the server safe-surfaces lever', () => {
  it('is honored, and is one-way', () => {
    setServerSafeSurfaces(false)
    expect(safeMode()).toBe(false)
    setServerSafeSurfaces(true)
    expect(safeMode()).toBe(true)
    expect(maxSurfaceLayer()).toBe(LAYER_CORE)
    setServerSafeSurfaces(false)
    expect(safeMode()).toBe(true)
  })
})

describe('the server meta tag', () => {
  it('puts the page in safe mode with no URL parameter at all', () => {
    const meta = document.createElement('meta')
    meta.setAttribute('name', 'gideon-safe-surfaces')
    meta.setAttribute('content', '1')
    act(() => { document.head.appendChild(meta) })
    try {
      expect(safeModeInUrl('#/dashboard')).toBe(false)
      expect(safeMode('#/dashboard')).toBe(true)
    } finally {
      meta.remove()
    }
  })
})
