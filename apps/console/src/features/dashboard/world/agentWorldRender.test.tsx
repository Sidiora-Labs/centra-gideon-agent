import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { AgentActivityEntity, AgentActivityFeed } from '../../../shared/data/useAgentActivity'


const SRC = join(process.cwd(), "src/features/dashboard/world")
const code = (f: string) =>
  readFileSync(join(SRC, f), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the world reaches no endpoint of its own', () => {
  const FILES = ['AgentWorld.tsx', 'worldScene.ts']

  it.each(FILES)('%s mentions no /api/ path', (f) => {
    const body = code(f)
    expect(body.length, `${f} came back empty — the scan is measuring nothing`).toBeGreaterThan(600)
    expect(body, `${f} must not name an endpoint`).not.toMatch(/["'`]\/api\//)
  })

  it.each(FILES)('%s opens no transport of its own', (f) => {
    const body = code(f)
    for (const banned of ['fetch(', 'XMLHttpRequest', 'new WebSocket', 'EventSource', 'navigator.sendBeacon']) {
      expect(body, `${f} must not use ${banned}`).not.toContain(banned)
    }
  })

  it.each(FILES)('%s never imports the api client', (f) => {
    expect(code(f)).not.toMatch(/from\s+['"][^'"]*lib\/api['"]/)
  })

  it('AgentWorld takes its data from useAgentActivity and no other hook', () => {
    const body = code('AgentWorld.tsx')
    expect(body).toContain('useAgentActivity()')
    for (const banned of ['useDashboardLive', 'useQuery', 'useChatSocket', 'useVisiblePoll']) {
      expect(body, `the world must not call ${banned} — it consumes ONE contract`).not.toContain(banned)
    }
  })

  it('AgentWorld takes no props — a world is handed a contract, not wired to a host', () => {
    const body = code('AgentWorld.tsx')
    expect(body).toMatch(/export function AgentWorld\(\)/)
  })
})


interface FakeCtx {
  calls: Record<string, number>
  ctx: CanvasRenderingContext2D
}

function fakeContext(): FakeCtx {
  const calls: Record<string, number> = {}
  const tally = (name: string) => (...__: unknown[]) => { calls[name] = (calls[name] ?? 0) + 1 }
  return {
    calls,
    ctx: {
      clearRect: tally('clearRect'), setTransform: tally('setTransform'),
      save: tally('save'), restore: tally('restore'),
      beginPath: tally('beginPath'), arc: tally('arc'),
      fill: tally('fill'), stroke: tally('stroke'),
      fillStyle: '', strokeStyle: '', lineWidth: 0, lineCap: 'butt',
      globalAlpha: 1, globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D,
  }
}

const ORIGINAL = {
  matchMedia: window.matchMedia,
  raf: window.requestAnimationFrame,
  caf: window.cancelAnimationFrame,
  getContext: HTMLCanvasElement.prototype.getContext,
}

function setReducedMotion(on: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: ((query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'),
      media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia,
  })
}

const entities: AgentActivityEntity[] = [
  { id: 'loop:a', kind: 'loop', state: 'working', title: 'Ship it', progress: 0.4, refs: { link: '#/loops/a' } },
  { id: 'loop:b', kind: 'loop', state: 'needs_input', title: 'Asks me', refs: { link: '#/loops/b' } },
  { id: 'session:c', kind: 'session', state: 'idle', title: 'Old chat', refs: { link: '#/chat/c' } },
]

const TONE_TOKENS = ['--color-ok', '--color-info', '--color-warn', '--color-danger',
  '--color-on-surface-low', '--color-outline-variant']

let frames: FrameRequestCallback[] = []
let instrument = fakeContext()
let feed: AgentActivityFeed

async function mountWorld() {
  vi.doMock('../../../shared/data/useAgentActivity', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    useAgentActivity: () => feed,
  }))
  const { AgentWorld } = await import('./AgentWorld')
  render(<AgentWorld />)
}

beforeEach(() => {
  vi.resetModules()
  frames = []
  instrument = fakeContext()
  feed = { entities, truncated: 0, error: null, loading: false, refresh: () => {} }
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true, writable: true, value: (cb: FrameRequestCallback) => frames.push(cb),
  })
  Object.defineProperty(window, 'cancelAnimationFrame', {
    configurable: true, writable: true, value: () => {},
  })
  HTMLCanvasElement.prototype.getContext = (() => instrument.ctx) as never
  for (const t of TONE_TOKENS) document.documentElement.style.setProperty(t, '#22c55e')
  for (const prop of ['clientWidth', 'clientHeight'] as const) {
    Object.defineProperty(HTMLCanvasElement.prototype, prop, {
      configurable: true, value: prop === 'clientWidth' ? 640 : 288,
    })
  }
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL.matchMedia })
  Object.defineProperty(window, 'requestAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.raf })
  Object.defineProperty(window, 'cancelAnimationFrame', { configurable: true, writable: true, value: ORIGINAL.caf })
  HTMLCanvasElement.prototype.getContext = ORIGINAL.getContext
  for (const t of TONE_TOKENS) document.documentElement.style.removeProperty(t)
  vi.restoreAllMocks()
})

describe('prefers-reduced-motion: reduce yields a static layout', () => {
  it('schedules NO animation frame at all', async () => {
    setReducedMotion(true)
    await mountWorld()
    expect(frames.length, 'reduced motion must not start an animation loop').toBe(0)
  })

  it('but it DOES paint — the floor under the claim above', async () => {
    setReducedMotion(true)
    await mountWorld()
    expect(instrument.calls.arc ?? 0, 'nothing was drawn').toBeGreaterThan(0)
    expect(instrument.calls.clearRect ?? 0).toBeGreaterThan(0)
  })

  it('POSITIVE CONTROL: with the preference OFF, the loop does run', async () => {
    setReducedMotion(false)
    await mountWorld()
    expect(frames.length, 'the animated path never scheduled a frame').toBe(1)
    const first = frames.shift()!
    first(16)
    expect(frames.length, 'the loop stopped after one frame').toBe(1)
  })

  it('the static paint is IDENTICAL across two mounts — nothing is time-dependent', async () => {
    setReducedMotion(true)
    await mountWorld()
    const a = { ...instrument.calls }
    instrument = fakeContext()
    HTMLCanvasElement.prototype.getContext = (() => instrument.ctx) as never
    vi.resetModules()
    await mountWorld()
    expect(instrument.calls).toEqual(a)
  })
})


let phase: 'loading' | 'loaded' = 'loading'
let bump: (() => void) | null = null

const LOADING_FEED: AgentActivityFeed =
  { entities: [], truncated: 0, error: null, loading: true, refresh: () => {} }

async function mountLate(loaded: AgentActivityFeed) {
  vi.doMock('../../../shared/data/useAgentActivity', async (orig) => {
    const react = await import('react')
    return {
      ...(await orig<Record<string, unknown>>()),
      useAgentActivity: () => {
        const [, force] = react.useState(0)
        bump = () => force((n) => n + 1)
        return phase === 'loading' ? LOADING_FEED : loaded
      },
    }
  })
  const { AgentWorld } = await import('./AgentWorld')
  render(<AgentWorld />)
}

async function settle() {
  phase = 'loaded'
  await act(async () => { bump!() })
}

describe('the world paints when the canvas arrives AFTER the first render', () => {
  beforeEach(() => { phase = 'loading'; bump = null; setReducedMotion(false) })

  it('sizes its backing store from the measured box and draws every node', async () => {
    const loaded: AgentActivityFeed =
      { entities, truncated: 0, error: null, loading: false, refresh: () => {} }
    expect(loaded.entities.length, 'nothing seeded — the rail would pass empty').toBeGreaterThan(0)

    await mountLate(loaded)
    expect(screen.queryByRole('img'), 'the canvas must be ABSENT while loading').toBeNull()
    expect(instrument.calls.arc ?? 0, 'nothing should be drawn yet').toBe(0)

    await settle()

    const canvas = screen.getByRole('img') as HTMLCanvasElement
    expect(canvas.width, 'backing store left at the HTML default — the painter never ran').not.toBe(300)
    expect(canvas.height, 'backing store left at the HTML default — the painter never ran').not.toBe(150)
    expect(canvas.width, 'backing store not sized from the measured box').toBe(640)
    expect(canvas.height).toBe(288)

    expect(frames.length, 'the late canvas never got an animation loop').toBe(1)
    await act(async () => { frames.shift()!(16) })

    expect(instrument.calls.clearRect ?? 0, 'the frame was never cleared').toBeGreaterThan(0)
    expect(instrument.calls.arc ?? 0, 'no node was ever drawn')
      .toBeGreaterThan(loaded.entities.length)
  })

  it('and the DOM fallback list is NOT shown once a real context is found', async () => {
    await mountLate({ entities, truncated: 0, error: null, loading: false, refresh: () => {} })
    await settle()
    expect(screen.queryByRole('list'), 'the world fell back despite having a 2d context').toBeNull()
  })

  it('VACUITY CONTROL: settling to an EMPTY feed draws nothing at all', async () => {
    await mountLate({ entities: [], truncated: 0, error: null, loading: false, refresh: () => {} })
    await settle()
    expect(screen.getByText(/Nothing is running\./)).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
    expect(instrument.calls.arc ?? 0).toBe(0)
  })

  it('a late canvas also gets an animation loop, not just one frame', async () => {
    await mountLate({ entities, truncated: 0, error: null, loading: false, refresh: () => {} })
    expect(frames.length, 'no loop should start before the canvas exists').toBe(0)
    await settle()
    expect(frames.length, 'the late canvas never got an animation loop').toBe(1)
  })
})

describe('an undeclared tone token cannot silently blank the scene', () => {
  it('nodes still paint when every colour token is missing', async () => {
    for (const t of TONE_TOKENS) document.documentElement.style.removeProperty(t)
    setReducedMotion(true)
    await mountWorld()
    expect(instrument.calls.arc ?? 0, 'a themeless document blanked the world').toBeGreaterThan(0)
  })
})

describe('the world is legible without seeing it', () => {
  it('the canvas carries the scene summary as its accessible name', async () => {
    setReducedMotion(true)
    await mountWorld()
    const img = screen.getByRole('img')
    expect(img.getAttribute('aria-label')).toContain('1 waiting on you')
    expect(img.getAttribute('aria-label')).toContain('1 working')
  })

  it('and the same facts are visible in text, not only announced', async () => {
    setReducedMotion(true)
    await mountWorld()
    expect(screen.getByText(/1 waiting on you, 1 working, 1 idle\./)).toBeInTheDocument()
  })

  it('with no drawing context the world falls back to a list, not a blank box', async () => {
    HTMLCanvasElement.prototype.getContext = (() => null) as never
    setReducedMotion(false)
    await mountWorld()
    expect(frames.length).toBe(0)
    for (const e of entities) expect(screen.getByText(e.title)).toBeInTheDocument()
  })
})

describe('the world says when it does not know', () => {
  it('a failed read is stated, never rendered as a calm empty scene', async () => {
    feed = { entities: [], truncated: 0, error: new Error('gateway down'), loading: false, refresh: () => {} }
    setReducedMotion(true)
    await mountWorld()
    expect(screen.getByText(/world is unknown right now/i)).toBeInTheDocument()
    expect(screen.queryByRole('img'), 'no scene is drawn over an unknown').toBeNull()
  })

  it('genuinely empty says so, and is NOT the same sentence as unknown', async () => {
    feed = { entities: [], truncated: 0, error: null, loading: false, refresh: () => {} }
    setReducedMotion(true)
    await mountWorld()
    expect(screen.getByText(/Nothing is running\./)).toBeInTheDocument()
    expect(screen.queryByText(/unknown right now/)).toBeNull()
  })

  it('while loading it holds the empty state back rather than claiming emptiness', async () => {
    feed = { entities: [], truncated: 0, error: null, loading: true, refresh: () => {} }
    setReducedMotion(true)
    await mountWorld()
    expect(screen.queryByText(/Nothing is running\./)).toBeNull()
  })
})
