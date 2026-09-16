import { describe, it, expect, beforeAll } from 'vitest'
import { render } from '@testing-library/react'
import { parseGenUi } from './parse'
import {
  allComponents,
  getComponent,
  library,
  validateInvocation,
  defineComponent,
} from './registry'
import { registerCoreGenUiComponents } from './components'
import { GenUiWidget } from './GenUiWidget'
import type { EmbedProps } from '../content/contentTypes'

beforeAll(() => registerCoreGenUiComponents())


describe('genui registry', () => {
  it('bundles a core component set spanning every group', () => {
    const groups = new Set(allComponents().map((c) => c.group))
    expect(groups).toEqual(new Set(['Layout', 'Data', 'Charts', 'Forms', 'Feedback']))
    for (const name of ['Stack', 'StatTile', 'Table', 'List', 'Bar', 'Callout', 'Button', 'Form']) {
      expect(getComponent(name), `${name} registered`).toBeTruthy()
    }
  })

  it('library.prompt() is derived from the registry (lists every component)', () => {
    const prompt = library.prompt()
    for (const def of allComponents()) {
      expect(prompt).toContain(def.name)
      for (const a of def.args.filter((x) => x.required)) expect(prompt).toContain(a.key)
    }
    defineComponent({
      name: 'ZZTestOnly', group: 'Feedback', description: 'test',
      args: [{ key: 'x', type: 'string', required: true }],
      component: () => null,
    })
    expect(library.prompt()).toContain('ZZTestOnly')
  })
})


describe('genui validation (drop-invalid, typed errors)', () => {
  it('unknown component → unknown-component', () => {
    const e = validateInvocation('NoSuchThing', [])
    expect(e?.kind).toBe('unknown-component')
    expect(e?.message).toContain('NoSuchThing')
  })

  it('missing required arg → missing-required naming the arg', () => {
    const e = validateInvocation('StatTile', ['label'])
    expect(e?.kind).toBe('missing-required')
    expect(e?.keys).toContain('value')
  })

  it('excess arg → excess-args naming the offender', () => {
    const e = validateInvocation('Callout', ['text', 'bogus'])
    expect(e?.kind).toBe('excess-args')
    expect(e?.keys).toEqual(['bogus'])
  })

  it('a well-formed invocation validates clean', () => {
    expect(validateInvocation('StatTile', ['label', 'value', 'delta'])).toBeNull()
  })
})


describe('genui DSL parser', () => {
  it('parses ids, components, scalar/array args, and refs', () => {
    const { lines, parseErrors } = parseGenUi(
      [
        'root = Stack(gap: "m", body: [a, b])',
        'a = StatTile(label: "Rev", value: "$1M", delta: 12)',
        'b = List(items: ["one", "two"])',
      ].join('\n'),
    )
    expect(parseErrors).toEqual([])
    expect(lines).toHaveLength(3)
    const root = lines[0]
    expect(root.component).toBe('Stack')
    expect(root.args.gap).toBe('m')
    expect(root.refs.body).toEqual(['a', 'b'])
    const a = lines[1]
    expect(a.args.value).toBe('$1M')
    expect(a.args.delta).toBe(12)
    expect(lines[2].args.items).toEqual(['one', 'two'])
  })

  it('records a malformed line as a parse error and keeps the rest', () => {
    const { lines, parseErrors } = parseGenUi('a = StatTile(label: "x", value: "y")\nthis is not a component')
    expect(lines).toHaveLength(1)
    expect(parseErrors).toHaveLength(1)
    expect(parseErrors[0].line).toBe(2)
  })
})


function renderWidget(content: string) {
  const props: EmbedProps = { content, title: 'Test' }
  return render(<GenUiWidget {...props} />)
}

describe('GenUiWidget rendering', () => {
  it('renders valid components in the host tree', () => {
    const { getByText } = renderWidget(
      [
        'root = Stack(gap: "m", body: [stat, note])',
        'stat = StatTile(label: "Revenue", value: "$1.2M", delta: 12)',
        'note = Callout(tone: "info", text: "Up 12%.")',
      ].join('\n'),
    )
    expect(getByText('Revenue')).toBeTruthy()
    expect(getByText('$1.2M')).toBeTruthy()
    expect(getByText('Up 12%.')).toBeTruthy()
  })

  it('ADVERSARIAL: an unknown component drops that line with a typed error and renders everything else (no null hole)', () => {
    const { getByText, queryByText, getAllByRole } = renderWidget(
      [
        'a = StatTile(label: "Kept", value: "42")',
        'b = Bogus(foo: "bar")',
        'c = Callout(tone: "ok", text: "Also kept")',
      ].join('\n'),
    )
    expect(getByText('Kept')).toBeTruthy()
    expect(getByText('Also kept')).toBeTruthy()
    const alerts = getAllByRole('alert')
    expect(alerts.some((a) => /Bogus/.test(a.textContent || ''))).toBe(true)
    expect(alerts.some((a) => /Unknown component/.test(a.textContent || ''))).toBe(true)
    expect(queryByText('bar')).toBeNull()
  })

  it('drops a missing-required line but keeps its valid siblings', () => {
    const { getByText, getAllByRole } = renderWidget(
      ['a = StatTile(label: "only-label")', 'b = Callout(tone: "info", text: "fine")'].join('\n'),
    )
    expect(getByText('fine')).toBeTruthy()
    expect(getAllByRole('alert').some((a) => /missing required/.test(a.textContent || ''))).toBe(true)
  })
})
