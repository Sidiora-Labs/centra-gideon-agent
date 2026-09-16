import { describe, it, expect, beforeAll, afterAll, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import {
  installAppSdk,
  resolvableAppSpecs,
  hasUiCapability,
  GenerativeWidget,
  type AppContext,
} from './appSdk'
import { ContributedPage } from '../../features/apps/ContributedPage'
import { Button } from '../../shared/ui/Button'
import { Surface } from '../../shared/ui/Surface'

const RealBlob = globalThis.Blob
const partText = new WeakMap<Blob, string>()
class RecordingBlob extends RealBlob {
  constructor(parts: BlobPart[] = [], opts?: BlobPropertyBag) {
    super(parts, opts)
    partText.set(this, String(parts[0] ?? ''))
  }
}

const realFetch = globalThis.fetch
let served = ''

beforeAll(() => {
  globalThis.Blob = RecordingBlob as unknown as typeof Blob
  ;(URL as { createObjectURL?: (b: Blob) => string }).createObjectURL = (b: Blob) =>
    'data:text/javascript;base64,' + Buffer.from(partText.get(b) ?? '', 'utf8').toString('base64')
  ;(URL as { revokeObjectURL?: (u: string) => void }).revokeObjectURL = () => {}
  globalThis.fetch = vi.fn(async () =>
    new Response(served, { status: 200, headers: { 'content-type': 'text/javascript' } }),
  ) as unknown as typeof fetch
  installAppSdk()
})

afterAll(() => {
  globalThis.Blob = RealBlob
  globalThis.fetch = realFetch
})

const FIXTURE_BUNDLE = `
import { createElement } from 'react'
import { Button, Surface } from '@gideon/app-sdk/ui'
export function mount() {
  return createElement(Surface, { tone: 'low', radius: 'xl' },
    createElement(Button, { variant: 'primary', size: 'sm' }, 'Save'))
}
`

const TOKENS_BUNDLE = `
import { createElement } from 'react'
import { Surface, readAppTheme } from '@gideon/app-sdk/ui'
export function mount() {
  const theme = readAppTheme()
  return createElement(Surface, { tone: 'low' },
    createElement('span', { 'data-mode': theme.mode, style: theme.cssVars }, 'themed'))
}
`

const GENUI_BUNDLE = `
import { createElement } from 'react'
import { GenerativeWidget } from '@gideon/app-sdk/genui'
export function mount() {
  return createElement(GenerativeWidget, {
    spec: 'note = Callout(tone: "info", text: "from the bundle")',
    title: 'App widget',
  })
}
`

function NativePage() {
  return (
    <Surface tone="low" radius="xl">
      <Button variant="primary" size="sm">Save</Button>
    </Surface>
  )
}

const MOTION_RESTING_DECLARATIONS = new Set(['opacity: 1', 'transform: none'])

function stripSettledMotionStyles(html: string): string {
  return html.replace(/ style="([^"]*)"/g, (attribute, body: string) => {
    const declarations = body.split(';').map((d) => d.trim()).filter(Boolean)
    const kept = declarations.filter((d) => !MOTION_RESTING_DECLARATIONS.has(d))
    if (kept.length === declarations.length) return attribute
    return kept.length === 0 ? '' : ` style="${kept.join('; ')};"`
  })
}

const pageMarkup = (root: HTMLElement) =>
  root.querySelector('button')!.closest('div[class]')!.outerHTML

async function awaitMotionSettled(root: HTMLElement) {
  await waitFor(
    () => {
      const style = root.querySelector('button > span:last-child')!.getAttribute('style') ?? ''
      expect(style).toContain('opacity: 1')
      expect(style).toContain('transform: none')
    },
    { timeout: 5_000 },
  )
}

const SETTLED_RESIDUE = /style="(opacity: 1; transform: none|transform: none; opacity: 1);"/

const declaring: AppContext = {
  name: 'ui-fixture',
  permissions: {},
  uiCapabilities: ['shell-primitives'],
}
const silent: AppContext = { name: 'ui-fixture', permissions: {}, uiCapabilities: [] }
const genuiApp: AppContext = {
  name: 'ui-fixture',
  permissions: {},
  uiCapabilities: ['generative-widget'],
}

describe('APE-11: a fixture app page renders from host primitives via the UI SDK', () => {
  it('is byte-identical to the same page written natively', async () => {
    served = FIXTURE_BUNDLE
    const app = render(<ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/page.js" />)
    await waitFor(() => expect(app.container.querySelector('button')).not.toBeNull())
    const native = render(<NativePage />)

    await awaitMotionSettled(app.container)
    await awaitMotionSettled(native.container)

    const contributed = pageMarkup(app.container)
    const baseline = pageMarkup(native.container)

    for (const [side, html] of [['contributed', contributed], ['native', baseline]] as const) {
      expect(html, `the ${side} side must carry the settled-motion residue`).toMatch(SETTLED_RESIDUE)
    }

    expect(baseline).toContain('<button')
    expect(baseline.length).toBeGreaterThan(120)

    expect(stripSettledMotionStyles(contributed)).toBe(stripSettledMotionStyles(baseline))
  })

  it('serialises to the same bytes read before or after the motion commit', async () => {
    served = FIXTURE_BUNDLE
    const app = render(<ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/settle.js" />)
    await waitFor(() => expect(app.container.querySelector('button')).not.toBeNull())
    const beforeCommit = pageMarkup(app.container)
    await awaitMotionSettled(app.container)
    const afterCommit = pageMarkup(app.container)

    expect(stripSettledMotionStyles(beforeCommit)).toBe(stripSettledMotionStyles(afterCommit))
    expect(afterCommit).toContain('<button')
    expect(afterCommit).toMatch(SETTLED_RESIDUE)
  })

  it('renders the host Button and Surface, not a lookalike', async () => {
    served = FIXTURE_BUNDLE
    const { container } = render(
      <ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/page2.js" />,
    )
    await waitFor(() => expect(container.querySelector('button')).not.toBeNull())
    const btn = container.querySelector('button')!
    expect(btn.closest('.bg-surface-low.rounded-xl')).not.toBeNull()
    expect(btn.textContent).toContain('Save')
  })

  it('leaves the /ui import UNRESOLVED for an app that declared no capability', async () => {
    served = FIXTURE_BUNDLE
    const { container, findByText } = render(
      <ContributedPage app={{ ...silent }} src="/apps/ui-fixture/ui/page3.js" />,
    )
    await findByText(/Failed to load ui-fixture/)
    expect(container.querySelector('button')).toBeNull()
  })

  it('reaches the TOKEN contract through the same subpath, not a guessed CSS variable', async () => {
    served = TOKENS_BUNDLE
    const { container } = render(
      <ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/tokens.js" />,
    )
    await waitFor(() => expect(container.querySelector('[data-mode]')).not.toBeNull())
    const span = container.querySelector('[data-mode]') as HTMLElement
    expect(['dark', 'light']).toContain(span.getAttribute('data-mode'))
    expect(span.getAttribute('style')).toContain('var(--color-surface)')
    expect(span.getAttribute('style')).toContain('--app-surface')
  })

  it('renders a generative widget imported through the gated /genui subpath', async () => {
    served = GENUI_BUNDLE
    const { container, findByText } = render(
      <ContributedPage app={{ ...genuiApp }} src="/apps/ui-fixture/ui/widget.js" />,
    )
    expect(await findByText('from the bundle')).toBeInTheDocument()
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('leaves /genui UNRESOLVED for an app that declared only shell-primitives', async () => {
    served = GENUI_BUNDLE
    const { findByText } = render(
      <ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/widget2.js" />,
    )
    await findByText(/Failed to load ui-fixture/)
  })
})

describe('APE-11: resolvableAppSpecs is the gate', () => {
  const ungated = ['react-dom/client', 'react-dom', 'react', '@gideon/app-sdk', 'lucide-react']

  it('always resolves the ungated head, declaration or not', () => {
    for (const spec of ungated) {
      expect(resolvableAppSpecs(undefined)).toContain(spec)
      expect(resolvableAppSpecs(declaring)).toContain(spec)
    }
  })

  it('adds /ui only for shell-primitives and /genui only for generative-widget', () => {
    expect(resolvableAppSpecs({ uiCapabilities: ['shell-primitives'] })).toContain('@gideon/app-sdk/ui')
    expect(resolvableAppSpecs({ uiCapabilities: ['shell-primitives'] })).not.toContain('@gideon/app-sdk/genui')
    expect(resolvableAppSpecs({ uiCapabilities: ['generative-widget'] })).toContain('@gideon/app-sdk/genui')
    expect(resolvableAppSpecs({ uiCapabilities: ['generative-widget'] })).not.toContain('@gideon/app-sdk/ui')
    expect(resolvableAppSpecs(silent)).not.toContain('@gideon/app-sdk/ui')
    expect(resolvableAppSpecs(silent)).not.toContain('@gideon/app-sdk/genui')
  })

  it('keeps the capability set explicit even with repeated declarations', () => {
    expect(resolvableAppSpecs(declaring)).toContain('@gideon/app-sdk/ui')
    expect(resolvableAppSpecs(declaring)).not.toContain('@gideon/app-sdk/genui')
    expect(resolvableAppSpecs(genuiApp)).toContain('@gideon/app-sdk/genui')
    expect(resolvableAppSpecs(genuiApp)).not.toContain('@gideon/app-sdk/ui')
    for (const app of [undefined, silent]) {
      expect(resolvableAppSpecs(app)).not.toContain('@gideon/app-sdk/ui')
      expect(resolvableAppSpecs(app)).not.toContain('@gideon/app-sdk/genui')
    }
  })

  it('hasUiCapability is false for absent, empty and unrelated declarations', () => {
    expect(hasUiCapability(undefined, 'shell-primitives')).toBe(false)
    expect(hasUiCapability({}, 'shell-primitives')).toBe(false)
    expect(hasUiCapability({ uiCapabilities: [] }, 'shell-primitives')).toBe(false)
    expect(hasUiCapability({ uiCapabilities: ['generative-widget'] }, 'shell-primitives')).toBe(false)
    expect(hasUiCapability({ uiCapabilities: ['shell-primitives'] }, 'shell-primitives')).toBe(true)
  })

  it('exposes BOTH subpaths on the host module map, so the gate is the only thing withholding them', () => {
    const map = (window as unknown as { __gideon_modules: Record<string, Record<string, unknown>> })
      .__gideon_modules
    expect(Object.keys(map['@gideon/app-sdk/ui'])).toEqual(
      expect.arrayContaining(['Button', 'Surface', 'useTheme', 'readAppTheme']),
    )
    expect(Object.keys(map['@gideon/app-sdk/genui'])).toEqual([
      'GenerativeWidget',
      'registerComponent',
      'unregisterComponents',
    ])
    expect(map['@gideon/app-sdk/ui']).not.toBe(map['@gideon/app-sdk'])
    expect(map['@gideon/app-sdk/ui'].useAppApi).toBeUndefined()
  })
})

describe('contributed bundles share the Gideon host runtime', () => {
  it('loads the base package and direct registry access together', async () => {
    served = `
      import { createElement } from 'react'
      import { readAppTheme } from '@gideon/app-sdk'
      export function mount() {
        const registry = window.__gideon_modules
        const shared = registry['@gideon/app-sdk'].readAppTheme === readAppTheme
        return createElement('span', { 'data-shared-sdk': String(shared) }, readAppTheme().mode)
      }
    `
    const { container } = render(
      <ContributedPage app={{ ...silent }} src="/apps/ui-fixture/ui/contributed-base.js" />,
    )
    await waitFor(() => expect(container.querySelector('[data-shared-sdk="true"]')).not.toBeNull())
  })

  it('renders contributed /ui imports with the host primitives', async () => {
    served = FIXTURE_BUNDLE
    const { container } = render(
      <ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/contributed-ui.js" />,
    )
    await waitFor(() => expect(container.querySelector('button')).not.toBeNull())
    expect(container.querySelector('button')!.closest('.bg-surface-low.rounded-xl')).not.toBeNull()
    expect(container.querySelector('button')!.textContent).toBe('Save')
  })

  it('refuses contributed /ui imports without shell-primitives', async () => {
    served = FIXTURE_BUNDLE
    const { container, findByText } = render(
      <ContributedPage app={{ ...silent }} src="/apps/ui-fixture/ui/contributed-ui-refused.js" />,
    )
    await findByText(/Failed to load ui-fixture/)
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders contributed /genui imports through the existing component registry', async () => {
    served = GENUI_BUNDLE
    const { container, findByText } = render(
      <ContributedPage app={{ ...genuiApp }} src="/apps/ui-fixture/ui/contributed-genui.js" />,
    )
    expect(await findByText('from the bundle')).toBeInTheDocument()
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('refuses contributed /genui imports when only shell-primitives is declared', async () => {
    served = GENUI_BUNDLE
    const { findByText } = render(
      <ContributedPage app={{ ...declaring }} src="/apps/ui-fixture/ui/contributed-genui-refused.js" />,
    )
    await findByText(/Failed to load ui-fixture/)
  })
})

describe('APE-11: the generative-widget contribution path', () => {
  it('renders a host-registered genui component from an app-supplied spec', () => {
    const { container, getByText } = render(
      <GenerativeWidget spec={'note = Callout(tone: "info", text: "From the app")'} title="App widget" />,
    )
    expect(getByText('From the app')).toBeInTheDocument()
    expect(getByText('App widget')).toBeInTheDocument()
    expect(container.querySelector('[role="alert"]')).toBeNull()
  })

  it('cannot reach a component the HOST never registered — the registry stays host-owned', () => {
    const { getByRole } = render(
      <GenerativeWidget spec={'x = AppOwnedThing(text: "escalation")'} title="App widget" />,
    )
    expect(getByRole('alert').textContent).toContain('Unknown component "AppOwnedThing"')
  })
})

describe('APE-11: the settled-motion normalisation the byte-identity test rests on', () => {
  const PRE_COMMIT = '<span class="relative inline-flex items-center gap-s">Save</span>'
  const MID_COMMIT =
    '<span class="relative inline-flex items-center gap-s" style="transform: none;">Save</span>'
  const SETTLED =
    '<span class="relative inline-flex items-center gap-s" style="opacity: 1; transform: none;">Save</span>'
  const SETTLED_REVERSED =
    '<span class="relative inline-flex items-center gap-s" style="transform: none; opacity: 1;">Save</span>'

  it('collapses every observed residue state onto the pre-commit bytes', () => {
    expect(stripSettledMotionStyles(SETTLED)).not.toBe(SETTLED)
    for (const state of [PRE_COMMIT, MID_COMMIT, SETTLED, SETTLED_REVERSED]) {
      expect(stripSettledMotionStyles(state)).toBe(PRE_COMMIT)
    }
  })

  it('strips ONLY declarations whose removal cannot change a rendered pixel', () => {
    const authored = [
      '<b style="font-variation-settings: &quot;wght&quot; 470;">x</b>',
      '<i style="opacity: 0.9; background: radial-gradient(circle at 50% 50%, color-mix(in srgb, var(--color-on-primary) 22%, transparent), transparent 60%);">x</i>',
      '<u style="transform: translateY(-4px);">x</u>',
      '<s style="opacity: 0;">x</s>',
      '<em style="opacity: 10;">x</em>',
      '<span style="--app-surface: var(--color-surface);">x</span>',
    ]
    for (const html of authored) expect(stripSettledMotionStyles(html)).toBe(html)

    expect(
      stripSettledMotionStyles('<span style="opacity: 1; background: red; transform: none;">x</span>'),
    ).toBe('<span style="background: red;">x</span>')

    const decoy = '<span data-note="opacity: 1; transform: none;" data-style="opacity: 1;">x</span>'
    expect(stripSettledMotionStyles(decoy)).toBe(decoy)
  })
})
