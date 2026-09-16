import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useRef } from 'react'
import { parseWidgetBlocks, findGenUiBlock, widgetlessText } from './blocks'
import { buildReactSrcdoc, buildSrcdoc, EDIT_MODE_SCRIPT_SOURCE, HOST_SCRIPT_SOURCE } from './widgetSrcdoc'
import { composeWidgetActionText, finishActionText, MAX_ACTION_TEXT_BYTES, publishWidgetAction } from './actionTurn'
import { readWidgetMessage, useWidgetActionBridge, useWidgetWire } from './useWidgetActionBridge'
import { sanitizeCssValue } from './cssSanitize'
import { deriveWidgetSlug, effectiveWidgetSlug } from './widgetSlug'
import { standaloneWidgetDocument, useWidgetExpansion, widgetLayout } from './widgetFrameState'

describe('widget content ownership', () => {
  it('keeps prose, attribute order and complete block boundaries', () => {
    const raw = 'Before\n<widget slug="sales" kind="genui" title="Revenue">\n tree \n</widget>\nAfter'
    expect(parseWidgetBlocks(raw)).toEqual([
      { type: 'md', content: 'Before\n' },
      { type: 'widget', title: 'Revenue', slug: 'sales', kind: 'genui', html: 'tree', complete: true },
      { type: 'md', content: '\nAfter' },
    ])
    expect(findGenUiBlock(raw)?.slug).toBe('sales')
    expect(widgetlessText(raw)).toBe('Before\n\nAfter')
  })
  it('holds incomplete content as a provisional widget only while streaming', () => {
    const raw = 'Lead<widget title="Pending"> <b>building'
    expect(parseWidgetBlocks(raw, true).at(-1)).toMatchObject({ type: 'widget', title: 'Pending', html: '<b>building', complete: false })
    expect(parseWidgetBlocks(raw)).toEqual([{ type: 'md', content: raw }])
    expect(findGenUiBlock('<widget kind="genui">tree')).toBeNull()
  })
  it('does not consume malformed tags or bare partial openings', () => {
    expect(parseWidgetBlocks(' <widget title=bad>x</widget>', true)).toEqual([{ type: 'md', content: ' <widget title=bad>x</widget>' }])
    expect(parseWidgetBlocks('text<widget', true)).toEqual([{ type: 'md', content: 'text<widget' }])
    expect(parseWidgetBlocks(' \n ')).toEqual([])
  })
  it('retains explicit identity and distinct persisted hash lanes', () => {
    expect(effectiveWidgetSlug({ explicitSlug: ' sales ', widgetIndex: 9 })).toBe('sales')
    expect(deriveWidgetSlug('2026-09-16', 1)).toMatch(/^w-[0-9a-f]{16}$/)
    expect(deriveWidgetSlug(undefined, 0)).toBe(deriveWidgetSlug('', 0))
    expect(deriveWidgetSlug('2026-09-16', 1)).not.toBe(deriveWidgetSlug('2026-09-16', 2))
  })
})

describe('iframe document envelope', () => {
  const opts = { html: '<button data-action="go">Go</button>', themeVars: { '--bg': '#123456' }, mode: 'dark' as const }
  it('retains restrictive CSP and independently selectable iteration/action scripts', () => {
    const bare = buildSrcdoc({ ...opts, includeHost: false })
    const document = new DOMParser().parseFromString(bare, 'text/html')
    const policy = document.querySelector('meta[http-equiv="Content-Security-Policy"]')?.getAttribute('content')
    for (const directive of ["default-src 'none'", "connect-src 'none'", "form-action 'none'", "base-uri 'none'", 'img-src data: blob:']) expect(policy).toContain(directive)
    expect(bare).not.toContain(HOST_SCRIPT_SOURCE)
    const normal = buildSrcdoc(opts)
    const edited = buildSrcdoc({ ...opts, editMode: true })
    expect(normal).toContain(HOST_SCRIPT_SOURCE)
    expect(normal).not.toContain(EDIT_MODE_SCRIPT_SOURCE)
    expect(edited.replace(`<script>\n${EDIT_MODE_SCRIPT_SOURCE}\n</script>\n`, '')).toBe(normal)
  })
  it('sanitizes both supplied theme keys and values at serialization', () => {
    const source = buildSrcdoc({ ...opts, transparentBody: true, themeVars: { '--bg': 'black', '--bad': 'red;}body{display:none', '--x:</style>': 'red' } })
    expect(source).toContain('background:transparent')
    expect(source).not.toContain('red;}body{display:none')
    expect(source).not.toContain('--x:')
    expect(source).not.toContain('--bad:')
  })
  it('keeps JSX script terminators inside the source script', () => {
    const source = buildReactSrcdoc({ jsx: 'const App = () => "</script><script>escape()</script>"', themeVars: {}, mode: 'light' })
    const document = new DOMParser().parseFromString(source, 'text/html')
    expect(document.querySelectorAll('script[type="text/babel"]')).toHaveLength(2)
    expect(document.querySelector('script[type="text/babel"]')?.textContent).toContain('<\\/script>')
    expect(source).not.toContain(HOST_SCRIPT_SOURCE)
  })
  it('exports through a sandboxed wrapper with escaped source and title', () => {
    const page = new DOMParser().parseFromString(standaloneWidgetDocument('<h1>"Hello"</h1>', '<script>title</script>'), 'text/html')
    expect(page.title).toBe('<script>title</script>')
    expect(page.querySelector('iframe')?.getAttribute('sandbox')).toBe('allow-scripts')
    expect(page.querySelector('iframe')?.srcdoc).toBe('<h1>"Hello"</h1>')
    expect(page.querySelectorAll('script')).toHaveLength(0)
  })
})

describe('CSS and action bounds', () => {
  it.each(['url(foo)', 'expression(1)', 'paint(foo)', 'red;display:none', '</style>', 'a'.repeat(201)])('refuses unsafe CSS %s', value => {
    expect(sanitizeCssValue(value)).toBe('')
  })
  it('keeps modern colors and trims whitespace', () => {
    expect(sanitizeCssValue(' oklch(0.7 0.1 250 / 50%) ')).toBe('oklch(0.7 0.1 250 / 50%)')
  })
  it('clips astral characters on a complete scalar boundary and retains the living-view suffix', () => {
    const text = finishActionText('😀'.repeat(9000))
    expect(new TextEncoder().encode(text).length).toBeLessThanOrEqual(MAX_ACTION_TEXT_BYTES)
    expect(text).toMatch(/😀…truncated$/u)
    expect(finishActionText('refresh', { saved: true, slug: 'sales' })).toBe('[UI] refresh (refresh artifact "sales" in place)')
    expect(composeWidgetActionText('submit', { huge: 1n })).toBeNull()
  })
})

describe('live frame and consumer routing', () => {
  it('chooses the most recently mounted chat, updates its callback, then hands ownership back', () => {
    const received: string[] = []
    function Owner({ label }: { label: string }) {
      useWidgetActionBridge(text => received.push(label + text))
      return null
    }
    const first = render(<Owner label="first:" />)
    const second = render(<Owner label="second:" />)
    act(() => publishWidgetAction('one'))
    second.rerender(<Owner label="updated:" />)
    act(() => publishWidgetAction('two'))
    second.unmount()
    act(() => publishWidgetAction('three'))
    expect(received).toEqual(['second:one', 'updated:two', 'first:three'])
    first.unmount()
  })
  it('reads current wire handlers and refuses React-style action delivery without opt-in', () => {
    const received: string[] = []
    function Wire({ label }: { label: string }) {
      const frame = useRef<HTMLIFrameElement>(null)
      useWidgetWire(frame, { onError: text => received.push(label + text) })
      return <iframe ref={frame} title="protocol" />
    }
    const view = render(<Wire label="old:" />)
    const frame = view.container.querySelector('iframe')!
    view.rerender(<Wire label="new:" />)
    act(() => window.dispatchEvent(new MessageEvent('message', { source: frame.contentWindow, data: { type: 'widget-error', message: 'failure' } })))
    expect(received).toEqual(['new:failure'])
    expect(readWidgetMessage(new MessageEvent('message', { source: window, data: { type: 'widget-error' } }), frame)).toBeNull()
  })
  it('bounds edit readback and preserves an own constructor key without changing prototypes', () => {
    const frame = document.createElement('iframe')
    document.body.append(frame)
    const values = Object.fromEntries([['constructor', 'blue'], ...Array.from({ length: 40 }, (_, index) => ['key' + index, 'x'.repeat(250)])])
    const parsed = readWidgetMessage(new MessageEvent('message', { source: frame.contentWindow, data: { type: 'widget-edit-values', values } }), frame)
    expect(parsed?.type).toBe('widget-edit-values')
    if (parsed?.type === 'widget-edit-values') {
      expect(Object.keys(parsed.values)).toHaveLength(32)
      expect(parsed.values.constructor).toBe('blue')
      expect(parsed.values.key0).toHaveLength(200)
    }
    frame.remove()
  })
  it('executes the child edit controller against an actual iframe DOM and fences foreign messages', () => {
    const frame = document.createElement('iframe')
    document.body.append(frame)
    const child = frame.contentWindow!
    new Function('window', 'document', 'parent', 'getComputedStyle', EDIT_MODE_SCRIPT_SOURCE)(child, frame.contentDocument, window, child.getComputedStyle.bind(child))
    const message = { type: '__edit_mode_set_keys', edits: [{ key: 'accent', value: '#123456' }, { key: 'a;bad', value: 'red' }] }
    child.dispatchEvent(new MessageEvent('message', { source: child, data: message }))
    expect(frame.contentDocument!.documentElement.style.length).toBe(0)
    child.dispatchEvent(new MessageEvent('message', { source: child.parent, data: message }))
    expect(frame.contentDocument!.documentElement.style.getPropertyValue('--accent')).toBe('#123456')
    expect(frame.contentDocument!.documentElement.style.length).toBe(1)
    frame.remove()
  })
})

describe('frame presentation state', () => {
  it('leaves space for neighboring prose and rejects narrow columns', () => {
    expect(widgetLayout(280, 1000)).toMatchObject({ float: 'left', width: 280 })
    expect(widgetLayout(280, 450)).toEqual({ width: '100%' })
    expect(widgetLayout(700, 900)).toEqual({ width: '100%' })
    expect(widgetLayout(null, 1000)).toEqual({ width: '100%' })
  })
  it('dismisses only the most recently expanded frame with Escape', () => {
    function Expansion({ name }: { name: string }) {
      const state = useWidgetExpansion()
      return <button onClick={state.toggle} aria-pressed={state.expanded}>{name}</button>
    }
    render(<><Expansion name="first" /><Expansion name="second" /></>)
    fireEvent.click(screen.getByRole('button', { name: 'first' }))
    fireEvent.click(screen.getByRole('button', { name: 'second' }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'first' }).getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByRole('button', { name: 'second' }).getAttribute('aria-pressed')).toBe('false')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'first' }).getAttribute('aria-pressed')).toBe('false')
  })
})
