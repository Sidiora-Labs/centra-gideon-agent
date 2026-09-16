import { render, act } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, afterEach } from 'vitest'
import { WidgetFrame } from './WidgetFrame'
import { ReactWidgetFrame } from './ReactWidgetFrame'
import { api } from '../../data/api'

vi.mock('../../data/api', () => ({
  api: {
    artifactExists: vi.fn(async () => false),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
  },
}))

beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:widget-wire-test'
    URL.revokeObjectURL = () => {}
  }
})

const published: string[] = []
const record = (e: Event) => { published.push(String((e as CustomEvent).detail?.text)) }
beforeAll(() => window.addEventListener('ne:widget-action', record as EventListener))
afterEach(() => { published.length = 0 })

function post(data: unknown, source: Window | null): void {
  act(() => {
    window.dispatchEvent(new MessageEvent('message', { data, source: source as MessageEventSource }))
  })
}

function foreignWindow(): Window | null {
  const el = document.createElement('iframe')
  document.body.appendChild(el)
  return el.contentWindow
}

describe('widget wire — WidgetFrame (the action producer)', () => {
  async function mount(html: string) {
    const view = render(<WidgetFrame html={html} title="W" />)
    await act(async () => {})
    const iframe = view.container.querySelector('iframe')
    if (!iframe) throw new Error('WidgetFrame rendered no iframe — the fixture never reached the wire')
    return { view, iframe, child: iframe.contentWindow }
  }

  it('turns a widget-action from THIS frame into an [UI] turn carrying the payload', async () => {
    const { child } = await mount('<button data-action="refresh">a</button>')
    post({ type: 'widget-action', action: 'refresh', payload: { a: 1 } }, child)
    expect(published).toEqual(['[UI] refresh: {"a":1}'])
  })

  it('drops the colon when the payload is empty or absent', async () => {
    const { child } = await mount('<button data-action="refresh">b</button>')
    post({ type: 'widget-action', action: 'refresh', payload: {} }, child)
    post({ type: 'widget-action', action: 'ping' }, child)
    expect(published).toEqual(['[UI] refresh', '[UI] ping'])
  })

  it('refuses a forged action from a foreign window — no turn', async () => {
    await mount('<button data-action="refresh">c</button>')
    post({ type: 'widget-action', action: 'exfiltrate', payload: { secret: 1 } }, foreignWindow())
    post({ type: 'widget-action', action: 'exfiltrate' }, null)
    post({ type: 'widget-action', action: 'exfiltrate' }, window)
    expect(published).toEqual([])
  })

  it('ignores a message whose type is not in the contract', async () => {
    const { child } = await mount('<button data-action="refresh">d</button>')
    post({ type: 'widget-exec', action: 'rm -rf', payload: {} }, child)
    post({ action: 'refresh', payload: {} }, child)
    post('widget-action', child)
    post(null, child)
    expect(published).toEqual([])
  })

  it('syncs height from THIS frame and ignores a non-numeric height', async () => {
    const { iframe, child } = await mount('<button data-action="refresh">e</button>')
    post({ type: 'widget-height', height: 421, width: 300 }, child)
    expect(iframe.style.height).toBe('421px')
    post({ type: 'widget-height', height: '9999' }, child)
    expect(iframe.style.height).toBe('421px')
  })

  it('floors a tiny reported height at the frame minimum', async () => {
    const { iframe, child } = await mount('<button data-action="refresh">f</button>')
    post({ type: 'widget-height', height: 4 }, child)
    expect(iframe.style.height).toBe('80px')
  })

  it('names the source artifact for a SAVED widget so the agent refreshes it in place', async () => {
    vi.mocked(api.artifactExists).mockResolvedValueOnce(true)
    const view = render(<WidgetFrame html="<button data-action='refresh'>g</button>" title="W" slug="sales-view" />)
    await act(async () => {})
    const child = view.container.querySelector('iframe')?.contentWindow ?? null
    post({ type: 'widget-action', action: 'refresh', payload: { range: '30d' } }, child)
    expect(published).toEqual(['[UI] refresh: {"range":"30d"} (refresh artifact "sales-view" in place)'])
  })
})

describe('widget wire — ReactWidgetFrame (height + error, same provenance rule)', () => {
  async function mount(jsx: string) {
    const view = render(<ReactWidgetFrame jsx={jsx} title="R" />)
    await act(async () => {})
    const iframe = view.container.querySelector('iframe')
    if (!iframe) throw new Error('ReactWidgetFrame rendered no iframe')
    return { view, iframe, child: iframe.contentWindow }
  }

  it('clamps a height from THIS frame into [MIN, MAX]', async () => {
    const { iframe, child } = await mount('const App = () => 1')
    post({ type: 'widget-height', height: 10 }, child)
    expect(iframe.style.height).toBe('80px')
    post({ type: 'widget-height', height: 99_999 }, child)
    expect(iframe.style.height).toBe('640px')
  })

  it('surfaces widget-error from THIS frame and refuses a foreign one', async () => {
    const { view, child } = await mount('const App = () => 2')
    post({ type: 'widget-error', message: 'boom' }, foreignWindow())
    expect(view.container.textContent).not.toContain('error')
    post({ type: 'widget-error', message: 'boom' }, child)
    expect(view.container.textContent).toContain('error')
  })
})
