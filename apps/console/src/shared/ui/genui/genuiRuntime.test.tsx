import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { parseGenUi } from './parse'
import { planGenUiProgram } from './programGraph'
import { GenUiNodes, GenUiWidget } from './GenUiWidget'
import { allComponents, componentLayer, defineComponent, getComponent, library, registerLayerComponent, removeComponentsFrom, validateInvocation } from './registry'
import { composeDualPayload, GenUiHostCtx, humanizeAction, planGenUiAction, routeGenUiAction, type GenUiProducer } from './actions'
import { chartSeries, formFields, formPayload, progressValue } from './componentState'
import { MAX_ACTION_TEXT_BYTES, WIDGET_ACTION_EVENT } from '../widget/actionTurn'
import { LAYER_APP, LAYER_CORE, LAYER_USER } from '../surfaces/layers'

const source = 'gideon-genui-runtime-check'
const savedHash = window.location.hash

afterEach(() => {
  removeComponentsFrom(source)
  removeComponentsFrom(source + '-other')
  window.location.hash = savedHash
})

describe('streaming DSL scanner', () => {
  it('parses nested table rows, quoted delimiters and scalar booleans', () => {
    const program = parseGenUi('table = Table(columns: ["Name", "Count"], rows: [["a,b", 2], ["c:d", 3]])\nflags = Thing(values: [true, false, 2])')
    expect(program.parseErrors).toEqual([])
    expect(program.lines[0].args.rows).toEqual([['a,b', 2], ['c:d', 3]])
    expect(program.lines[1].args.values).toEqual([true, false, 2])
  })

  it('recognizes quotes after escaped backslashes and unescapes embedded quotes', () => {
    const body = String.raw`a = Callout(text: "path\\", tone: "ok")
b = Callout(text: "say \"go\", now")`
    const program = parseGenUi(body)
    expect(program.parseErrors).toEqual([])
    expect(program.lines[0].args.text).toBe(String.raw`path\\`)
    expect(program.lines[0].args.tone).toBe('ok')
    expect(program.lines[1].args.text).toBe('say "go", now')
  })

  it('retains valid siblings and source line numbers around unfinished input', () => {
    const program = parseGenUi('# heading\n\na = Callout(text: "Ready")\nb = List(items: ["pending")\n// end')
    expect(program.lines.map(line => line.line)).toEqual([3])
    expect(program.parseErrors).toEqual([{ line: 4, text: 'b = List(items: ["pending")', message: 'incomplete quoted or nested value' }])
  })

  it('keeps references distinct from literal strings and handles an empty array', () => {
    const line = parseGenUi('root = Thing(child: next, body: [first, second], literal: "next", empty: [])').lines[0]
    expect(line.refs).toEqual({ child: ['next'], body: ['first', 'second'] })
    expect(line.args).toEqual({ literal: 'next', empty: [] })
    expect(line.argKeys).toEqual(['child', 'body', 'literal', 'empty'])
  })

  it('preserves tolerant missing-key arguments but gives the last named value ownership', () => {
    const line = parseGenUi('a = Thing(ignored, : "empty key", body: next, body: "literal")').lines[0]
    expect(line.argKeys).toEqual(['body', 'body'])
    expect(line.refs.body).toBeUndefined()
    expect(line.args.body).toBe('literal')
  })

  it('stores special property names without mutating dictionary prototypes', () => {
    const line = parseGenUi('a = Thing(__proto__: [next], constructor: "safe")').lines[0]
    expect(Object.getPrototypeOf(line.args)).toBeNull()
    expect(Object.getPrototypeOf(line.refs)).toBeNull()
    expect(line.refs.__proto__).toEqual(['next'])
    expect(line.args.constructor).toBe('safe')
  })
})

describe('reference planning and real rendering', () => {
  it('keeps root order and uses the last definition of a duplicate id', () => {
    const plan = planGenUiProgram(parseGenUi('a = Callout(text: "old")\nb = Stack(body: [a])\na = Callout(text: "new")'))
    expect(plan.roots.map(line => line.id)).toEqual(['b'])
    expect(plan.byId.get('a')?.args.text).toBe('new')
  })

  it('surfaces a closed reference cycle without dropping independent roots', () => {
    render(<GenUiNodes content={'a = Stack(body: [b])\nb = Card(body: [a])\nkept = Callout(text: "Still here")'} />)
    expect(screen.getByText('Still here')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Reference cycle: a → b → a.')
  })

  it('waits for forward references and paints them when the stream reaches their definition', () => {
    const host = render(<GenUiNodes content="root = Stack(body: [later])" />)
    expect(screen.queryByRole('alert')).toBeNull()
    host.rerender(<GenUiNodes content={'root = Stack(body: [later])\nlater = Badge(text: "Arrived")'} />)
    expect(screen.getByText('Arrived')).toBeInTheDocument()
  })

  it('renders nested table values and escapes model-authored HTML', () => {
    const host = render(<GenUiNodes content={'table = Table(columns: ["Name", "Count"], rows: [["<img src=x>", 2], ["Gideon", 4]])'} />)
    expect(screen.getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['Name', 'Count'])
    expect(screen.getAllByRole('cell').map(cell => cell.textContent)).toEqual(['<img src=x>', '2', 'Gideon', '4'])
    expect(host.container.querySelector('img')).toBeNull()
  })

  it('exposes progress bounds and chart values through actual accessibility attributes', () => {
    render(<GenUiNodes content={'p = ProgressBar(value: 125, label: "Complete")\nb = Bar(data: [0, 5, 10], labels: ["A", "B", "C"])'} />)
    expect(screen.getByRole('progressbar', { name: 'Complete' })).toHaveAttribute('aria-valuenow', '100')
    expect(screen.getByRole('img', { name: 'Bar chart: A: 0, B: 5, C: 10' })).toBeInTheDocument()
  })
})

describe('registry contracts', () => {
  it('keeps core overrides in the same declaration position', () => {
    const original = getComponent('Badge')!
    const names = allComponents().map(component => component.name)
    try {
      defineComponent({ ...original, description: 'Updated badge description' })
      expect(allComponents().map(component => component.name)).toEqual(names)
      expect(library.prompt()).toContain('Updated badge description')
      expect(componentLayer('Badge')).toBe(LAYER_CORE)
    } finally { defineComponent(original) }
  })

  it('uses real registered components to enforce source ownership and removal', () => {
    const badge = { ...getComponent('Badge')!, name: 'RuntimeBadge' }
    expect(registerLayerComponent(badge, { source, layer: LAYER_APP })).toEqual({ ok: true })
    expect(registerLayerComponent(badge, { source: source + '-other', layer: LAYER_USER })).toMatchObject({ ok: false, code: 'shadows-layer' })
    expect(registerLayerComponent(badge, { source, layer: LAYER_APP })).toEqual({ ok: true })
    expect(library.prompt()).toContain(`[from the ${source} app]`)
    render(<GenUiNodes content={'badge = RuntimeBadge(text: "Layered content")'} />)
    expect(screen.getByText('Layered content')).toBeInTheDocument()
    expect(removeComponentsFrom(source)).toBe(1)
    expect(getComponent('RuntimeBadge')).toBeUndefined()
    expect(library.prompt()).not.toContain('RuntimeBadge')
    expect(removeComponentsFrom('')).toBe(0)
  })

  it('refuses core shadowing and registrations in safe mode', () => {
    const badge = getComponent('Badge')!
    expect(registerLayerComponent(badge, { source, layer: LAYER_APP })).toMatchObject({ ok: false, code: 'shadows-core' })
    window.location.hash = '#/dashboard?safe=1'
    expect(registerLayerComponent({ ...badge, name: 'SafeBadge' }, { source, layer: LAYER_APP })).toMatchObject({ ok: false, code: 'layer-disabled' })
  })

  it('prevents a previously registered layer from rendering after safe mode is enabled', () => {
    registerLayerComponent({ ...getComponent('Badge')!, name: 'SafeBadge' }, { source, layer: LAYER_APP })
    window.location.hash = '#/dashboard?safe=1'
    render(<GenUiNodes content={'a = SafeBadge(text: "Hidden")\nb = Badge(text: "Core remains")'} />)
    expect(screen.queryByText('Hidden')).toBeNull()
    expect(screen.getByRole('alert')).toHaveTextContent('unavailable in safe mode')
    expect(screen.getByText('Core remains')).toBeInTheDocument()
  })

  it('preserves validation priority and the ordered keys in correction messages', () => {
    expect(validateInvocation('StatTile', ['unexpected'])?.keys).toEqual(['label', 'value'])
    expect(validateInvocation('StatTile', ['label', 'value', 'second', 'first'])?.keys).toEqual(['second', 'first'])
    expect(validateInvocation('NeverRegistered', ['label'])?.kind).toBe('unknown-component')
  })
})

describe('action plans and payload state', () => {
  it('derives routing identities only from the host, keeping model values inside the answer', () => {
    const dual = composeDualPayload({ action: 'submit', label: 'Submit' })!
    const raw = { action: 'override', payload: { runId: 'attacker', token: 'attacker', viewId: 'attacker' } }
    const gate = planGenUiAction(dual, { kind: 'workflow-gate', runId: 'owned', token: 'secret' }, raw)
    expect(gate).toEqual({ kind: 'workflow-gate', runId: 'owned', request: { answer: raw.payload, resume_token: 'secret' } })
    const tile = planGenUiAction(dual, { kind: 'tile', viewId: 'dashboard', ref: 'artifact:owned' }, raw)
    expect(tile).toEqual({ kind: 'tile', viewId: 'dashboard', request: { ref: 'artifact:owned', action: raw.action, payload: raw.payload } })
  })

  it('keeps empty gate answers human-readable and chat plans free of endpoint identities', () => {
    const dual = composeDualPayload({ action: 'approve', label: 'Approve request' })!
    const producers: GenUiProducer[] = [{ kind: 'workflow-gate', runId: 'run', token: 'token' }, { kind: 'chat' }]
    expect(planGenUiAction(dual, producers[0], { action: 'approve', payload: {} })).toMatchObject({ request: { answer: 'Approve request' } })
    expect(planGenUiAction(dual, producers[1], { action: 'approve' })).toEqual({ kind: 'chat', text: '[UI] approve', label: 'Approve request' })
  })

  it('clips machine bytes while keeping the visible label and rejects bigint values', () => {
    const dual = composeDualPayload({ action: 'send', label: 'Send details', payload: { text: '😀'.repeat(10000) } })!
    expect(new TextEncoder().encode(dual.llmFriendlyMessage).length).toBeLessThanOrEqual(MAX_ACTION_TEXT_BYTES)
    expect(dual.llmFriendlyMessage).toContain('…truncated')
    expect(dual.humanFriendlyMessage).toBe('Send details')
    expect(composeDualPayload({ action: 'send', payload: { count: 1n } })).toBeNull()
    expect(humanizeAction('sendDraft_now')).toBe('Send Draft now')
  })

  it('collects every declared field and preserves special names as ordinary values', () => {
    const fields = formFields(['amount', 'vendor', '__proto__', 'constructor'])
    const values = Object.fromEntries([['amount', '12'], ['__proto__', 'plain data']])
    expect(formPayload(fields, values)).toEqual({ amount: '12', vendor: '', ['__proto__']: 'plain data', constructor: '' })
  })

  it('normalizes nonfinite chart values and clamps progress without poisoning styles', () => {
    expect(chartSeries([Infinity, -4, '5', 'bad'], ['a', 'b']).map(point => point.height)).toEqual([2, 2, 100, 2])
    expect([NaN, -10, 50, 200].map(progressValue)).toEqual([0, 0, 50, 100])
  })
})

describe('real chat action dispatch', () => {
  it('publishes exactly one event with both human and machine messages', async () => {
    const events: unknown[] = []
    const listener = (event: Event) => events.push((event as CustomEvent).detail)
    window.addEventListener(WIDGET_ACTION_EVENT, listener)
    try {
      const dual = composeDualPayload({ action: 'refresh', label: 'Refresh results' })!
      expect(await routeGenUiAction(dual, { kind: 'chat' }, { action: 'refresh' })).toEqual({ ok: true, outcome: 'chat-turn' })
      expect(events).toEqual([{ text: '[UI] refresh', label: 'Refresh results' }])
    } finally { window.removeEventListener(WIDGET_ACTION_EVENT, listener) }
  })

  it('keeps entered form values as content streams and suppresses duplicate submits', async () => {
    const events: Array<{ text: string; label: string }> = []
    const listener = (event: Event) => events.push((event as CustomEvent).detail)
    window.addEventListener(WIDGET_ACTION_EVENT, listener)
    const resolutions: string[] = []
    const producer = { producer: { kind: 'chat' as const }, onResolved: () => resolutions.push('resolved') }
    const tree = (fields: string) => <GenUiHostCtx.Provider value={producer}>
      <GenUiWidget title="Expense" slug="expenses" content={`f = Form(fields: [${fields}], action: "save", submit: "Save expense")`} />
    </GenUiHostCtx.Provider>
    try {
      const host = render(tree('"amount"'))
      fireEvent.change(screen.getByLabelText('Amount'), { target: { value: '12.40' } })
      host.rerender(tree('"amount", "vendor"'))
      expect(screen.getByLabelText('Amount')).toHaveValue('12.40')
      await act(async () => {
        fireEvent.submit(screen.getByLabelText('Amount').closest('form')!)
        fireEvent.submit(screen.getByLabelText('Amount').closest('form')!)
      })
      expect(events).toHaveLength(1)
      expect(events[0].label).toBe('Save expense')
      expect(events[0].text).toContain('"amount":"12.40","vendor":""')
      expect(events[0].text).toContain('refresh artifact "expenses" in place')
      expect(resolutions).toEqual(['resolved'])
    } finally { window.removeEventListener(WIDGET_ACTION_EVENT, listener) }
  })
})
