import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { AppSummary } from '../../shared/data/api'
import {
  componentsModuleUrl,
  contributesComponents,
  loadAppComponents,
  resetAppGenUiLayer,
  syncAppGenUiComponents,
} from './appGenUiLayer'
import { allComponents, getComponent, library } from '../../shared/ui/genui/registry'
import { registerCoreGenUiComponents } from '../../shared/ui/genui/components'

vi.mock('../../shared/data/api', () => ({ api: { apps: vi.fn(async () => []) } }))

const moduleStub: { register?: unknown } = {}
vi.mock('./appSdk', async (importOriginal) => {
  const real = await importOriginal<typeof import('./appSdk')>()
  return { ...real, loadContributedModule: vi.fn(async () => moduleStub) }
})

registerCoreGenUiComponents()

const GAUGE = {
  name: 'AcmeGauge',
  group: 'Data' as const,
  description: 'gauge',
  args: [{ key: 'value', type: 'number' as const, required: true }],
  component: () => null,
}

function app(over: Partial<AppSummary> = {}): AppSummary {
  return {
    name: 'acme',
    displayName: 'Acme',
    version: '1.0.0',
    description: '',
    enabled: true,
    origin: 'path',
    icon: '',
    hasBackend: false,
    hasUI: false,
    uiPages: [],
    uiComponents: 'genui.mjs',
    uiCapabilities: ['generative-component'],
    isProvider: false,
    providerType: '',
    hasConfig: false,
    permissions: {},
    tags: [],
    backendRunning: false,
    backendPort: null,
    ...over,
  } as AppSummary
}

function registersGauge(name = 'AcmeGauge') {
  return {
    register: (sdk: { registerComponent: (ctx: unknown, def: unknown) => unknown }, ctx: unknown) =>
      sdk.registerComponent(ctx as never, { ...GAUGE, name } as never),
  }
}

beforeEach(() => {
  resetAppGenUiLayer()
  delete moduleStub.register
  window.location.hash = '#/dashboard'
})

describe('eligibility', () => {
  it('needs enabled + a module + the capability, all three', () => {
    expect(contributesComponents(app())).toBe(true)
    expect(contributesComponents(app({ enabled: false }))).toBe(false)
    expect(contributesComponents(app({ uiComponents: '' }))).toBe(false)
    expect(contributesComponents(app({ uiCapabilities: [] }))).toBe(false)
  })

  it('is NOT granted by the widget capability', () => {
    expect(contributesComponents(app({ uiCapabilities: ['generative-widget'] }))).toBe(false)
  })

  it('serves the module off the app-ui asset route', () => {
    expect(componentsModuleUrl('acme', 'genui.mjs')).toBe('/apps/acme/ui/genui.mjs')
    expect(componentsModuleUrl('acme', '/genui.mjs')).toBe('/apps/acme/ui/genui.mjs')
  })
})

describe('loading an app component layer', () => {
  it('registers what the module registers, and it shows up in the prompt', async () => {
    Object.assign(moduleStub, registersGauge())
    expect(await loadAppComponents(app())).toBe(1)
    expect(getComponent('AcmeGauge')?.source).toBe('acme')
    expect(library.prompt()).toContain('AcmeGauge')
  })

  it('registers NOTHING for an app that never declared the capability', async () => {
    Object.assign(moduleStub, registersGauge())
    expect(await loadAppComponents(app({ uiCapabilities: ['generative-widget'] }))).toBe(0)
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })

  it('registers nothing in safe mode', async () => {
    window.location.hash = '#/dashboard?safe=1'
    Object.assign(moduleStub, registersGauge())
    expect(await loadAppComponents(app())).toBe(0)
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })

  it('survives a module with no register() export', async () => {
    expect(await loadAppComponents(app())).toBe(0)
  })

  it('counts only the registrations that were ACCEPTED', async () => {
    const spy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    moduleStub.register = (sdk: { registerComponent: (c: unknown, d: unknown) => unknown }, ctx: unknown) => {
      sdk.registerComponent(ctx as never, { ...GAUGE, name: 'Table' } as never)
      sdk.registerComponent(ctx as never, { ...GAUGE, name: 'AcmeGauge' } as never)
    }
    expect(await loadAppComponents(app())).toBe(1)
    expect(getComponent('Table')?.source).toBe('')
    expect(getComponent('AcmeGauge')?.source).toBe('acme')
    spy.mockRestore()
  })

  it('does not re-run a module it already loaded', async () => {
    Object.assign(moduleStub, registersGauge())
    expect(await loadAppComponents(app())).toBe(1)
    expect(await loadAppComponents(app())).toBe(0)
  })
})

describe('the sync pass', () => {
  it('removes a DISABLED app’s components', async () => {
    Object.assign(moduleStub, registersGauge())
    await loadAppComponents(app())
    expect(getComponent('AcmeGauge')).toBeTruthy()

    await syncAppGenUiComponents([app({ enabled: false })])
    expect(getComponent('AcmeGauge')).toBeUndefined()
    expect(library.prompt()).not.toContain('AcmeGauge')
  })

  it('removes them when the capability is revoked, not only when the app is off', async () => {
    Object.assign(moduleStub, registersGauge())
    await loadAppComponents(app())
    await syncAppGenUiComponents([app({ uiCapabilities: [] })])
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })

  it('leaves the core set alone', async () => {
    const core = allComponents().filter((c) => c.layer === 0).length
    Object.assign(moduleStub, registersGauge())
    await syncAppGenUiComponents([app()])
    await syncAppGenUiComponents([app({ enabled: false })])
    expect(allComponents().filter((c) => c.layer === 0).length).toBe(core)
    expect(getComponent('Table')).toBeTruthy()
  })

  it('does nothing at all in safe mode', async () => {
    window.location.hash = '#/dashboard?safe=1'
    Object.assign(moduleStub, registersGauge())
    expect(await syncAppGenUiComponents([app()])).toBe(0)
    expect(getComponent('AcmeGauge')).toBeUndefined()
  })
})

it('removes uninstalled apps when they disappear from the authoritative list', async () => {
  Object.assign(moduleStub, registersGauge())
  await loadAppComponents(app())
  await syncAppGenUiComponents([])
  expect(getComponent('AcmeGauge')).toBeUndefined()
})

it('reloads a changed module identity after removing its old registrations', async () => {
  Object.assign(moduleStub, registersGauge())
  expect(await loadAppComponents(app())).toBe(1)
  expect(await loadAppComponents(app({ version: '2.0.0', uiComponents: 'updated.mjs' }))).toBe(1)
  expect(getComponent('AcmeGauge')?.source).toBe('acme')
})

it('rejects a delayed registration after the app was disabled', async () => {
  let resume!: () => void
  let entered!: () => void
  const started = new Promise<void>((resolve) => { entered = resolve })
  const pending = new Promise<void>((resolve) => { resume = resolve })
  let accepted: unknown
  moduleStub.register = async (sdk: { registerComponent: (context: unknown, definition: unknown) => unknown }, context: unknown) => {
    entered()
    await pending
    accepted = sdk.registerComponent(context, GAUGE)
  }
  const loading = loadAppComponents(app())
  await started
  await syncAppGenUiComponents([app({ enabled: false })])
  resume()
  expect(await loading).toBe(0)
  expect(accepted).toMatchObject({ ok: false })
  expect(getComponent('AcmeGauge')).toBeUndefined()
})
