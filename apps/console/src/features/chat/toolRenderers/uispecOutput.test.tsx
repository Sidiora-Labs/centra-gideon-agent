import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import type { ToolSegment } from '../chatTypes'
import { connectedUISpecContracts, renderAuiResult, resolveToolUISpec } from '../auiResultRegistry'
import { donorStructures } from '../../../shared/ui/assistant-ui/generative/donorStructures'
import { boundRecord } from '../../../shared/ui/assistant-ui/qualification/generativeLiveData'
import type { GenerativeTemplate } from '../../../shared/ui/assistant-ui/generative/uispec'
import { renderToolOutput } from './registry'

const prefix = 'Show this to the user by embedding the widget block below in your reply:\n\n'
function envelope(template: GenerativeTemplate) {
  const { recordId, bindings } = boundRecord(template)
  return { schemaVersion: 1, template, recordId, bindings }
}
function output(data: unknown, title = 'Visualization') {
  const body = JSON.stringify(data).replaceAll('<', '\\u003c').replaceAll('>', '\\u003e').replaceAll('&', '\\u0026')
  return `${prefix}<widget kind="uispec" title="${title}">\n${body}\n</widget>`
}
function segment(data: ReturnType<typeof envelope>, overrides: Partial<ToolSegment> = {}): ToolSegment {
  return { kind: 'tool', id: 'visualize-call-17', tool: 'visualize', done: true, ok: true,
    input: JSON.stringify({ data: { generative_ui: data } }), output: output(data), ...overrides }
}
function mount(seg: ToolSegment) { return render(<>{renderToolOutput(seg)}</>) }


describe('real visualize tool output to connected ToolCard renderer', () => {
  it('derives the connected contract from all 22 donor templates', () => {
    const templates = donorStructures.map(item => item.slug)
    expect(templates).toHaveLength(22)
    expect(connectedUISpecContracts.visualize).toEqual(templates)
    for (const template of templates) {
      const data = envelope(template as GenerativeTemplate)
      const spec = resolveToolUISpec(segment(data), connectedUISpecContracts)
      expect(spec).toMatchObject({ template, producer: 'tool:visualize', recordId: data.recordId })
      expect(spec?.tree).toBeTruthy()
    }
  })

  it('renders real record bindings from the exact _visualize text wrapper', () => {
    const data = envelope('weather-current')
    mount(segment(data))
    expect(screen.getByRole('region', { name: 'Live weather-current result' })).toBeInTheDocument()
    expect(screen.getByText('Live children.2.value')).toBeInTheDocument()
    expect(document.body.textContent).not.toContain('Show this to the user by embedding')
  })

  it('makes unsupported task actions unavailable without backend capabilities', () => {
    const data = envelope('create-task')
    mount(segment(data))
    expect(screen.getByRole('region', { name: 'Live create-task result' })).toBeInTheDocument()
    expect(screen.getAllByText(/No connected provider or authorized route/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: /create task/i })).toBeNull()
  })

  it('reads live inputObj and accepts the same JSON record regardless of object key order', () => {
    const data = envelope('chart-bars')
    data.bindings['children.0.series'] = [{ label: 'Measured', values: [1, 2] }]
    const ordered = { bindings: data.bindings, recordId: data.recordId, template: data.template, schemaVersion: 1 }
    const spec = resolveToolUISpec(segment(data, { input: undefined,
      inputObj: { data: { generative_ui: ordered } } }), connectedUISpecContracts)
    expect(spec?.template).toBe('chart-bars')
    expect(resolveToolUISpec(segment(data, { input: undefined }), connectedUISpecContracts)?.template).toBe('chart-bars')
  })

  it('bounds deeply nested input and output arrays before constructing a view', () => {
    const data = envelope('chart-bars')
    let nested: unknown = 'measured'
    for (let level = 0; level < 9; level++) nested = [nested]
    data.bindings['children.0.series'] = nested
    expect(resolveToolUISpec(segment(data), connectedUISpecContracts)).toBeNull()
  })

  it('rejects changed, absent, malformed, or conflicting call input', () => {
    const data = envelope('weather-current')
    const changed = { ...data, recordId: 'other-record' }
    expect(resolveToolUISpec(segment(data, { input: JSON.stringify({ data: { generative_ui: changed } }) }), connectedUISpecContracts)).toBeNull()
    expect(resolveToolUISpec(segment(data, { input: JSON.stringify({ data: {} }) }), connectedUISpecContracts)).toBeNull()
    expect(resolveToolUISpec(segment(data, { input: 'not json' }), connectedUISpecContracts)).toBeNull()
    expect(resolveToolUISpec(segment(data, { inputObj: { data: { generative_ui: changed } } }), connectedUISpecContracts)).toBeNull()
    expect(resolveToolUISpec(segment(data, { inputObj: [] }), connectedUISpecContracts)).toBeNull()
    expect(resolveToolUISpec(segment(data, { inputObj: { data: { generative_ui: { ...data, bindings: {} } } } }), connectedUISpecContracts)).toBeNull()
  })

  it.each([
    ['missing prefix', (value: string) => value.slice(prefix.length)],
    ['extra prose', (value: string) => `Untrusted prose\n${value}`],
    ['missing close', (value: string) => value.replace('</widget>', '')],
    ['second block', (value: string) => `${value}\n<widget kind="uispec">{}</widget>`],
    ['wrong kind', (value: string) => value.replace('kind="uispec"', 'kind="genui"')],
    ['newline in title', (value: string) => value.replace('Visualization', 'Bad\ntitle')],
    ['invalid JSON', (value: string) => value.replace('"schemaVersion":1', '"schemaVersion":')],
  ])('refuses %s instead of mounting a donor view', (_case, transform) => {
    const data = envelope('weather-current')
    const seg = segment(data)
    const invalid = { ...seg, output: transform(seg.output!) }
    expect(resolveToolUISpec(invalid, connectedUISpecContracts)).toBeNull()
    expect(mount(invalid).container.querySelector('[data-gideon-uispec]')).toBeNull()
  })

  it('requires complete v1 bindings and rejects extra structured fields', () => {
    const data = envelope('weather-current')
    const invalid = [
      { ...data, schemaVersion: 2 },
      { ...data, bindings: {} },
      { ...data, extra: 'unverified' },
      { ...data, template: 'missing-template' },
    ]
    for (const candidate of invalid) {
      expect(resolveToolUISpec(segment(data, { output: output(candidate) }), connectedUISpecContracts)).toBeNull()
    }
  })

  it.each([
    ['unfinished', { done: false }], ['failed', { ok: false }], ['truncated', { truncated: true }],
    ['empty', { output: '' }], ['oversized', { output: 'x'.repeat(1_000_001) }],
  ])('does not mount %s output', (_case, override) => {
    const data = envelope('weather-current')
    const seg = segment(data, override)
    expect(resolveToolUISpec(seg, connectedUISpecContracts)).toBeNull()
    expect(mount(seg).container.querySelector('[data-gideon-uispec]')).toBeNull()
  })

  it('does not render a forged visualize wrapper from a different tool identity', () => {
    const data = envelope('weather-current')
    for (const tool of ['read_file', 'mcp__other__visualize', 'mcp__gideon-artifacts__visualize']) {
      const seg = segment(data, { tool })
      expect(resolveToolUISpec(seg, connectedUISpecContracts)).toBeNull()
      expect(mount(seg).container.querySelector('[data-gideon-uispec]')).toBeNull()
    }
  })

  it('keeps native output ahead of UISpec and diff/terminal fallback for unrelated tools', () => {
    const data = envelope('weather-current')
    expect(mount(segment(data, { tool: 'bash', inputObj: { command: 'cat result' } })).container.querySelector('[data-gideon-uispec]')).toBeNull()
    const diff = ['--- a/src/live.ts', '+++ b/src/live.ts', '@@ -1 +1 @@', '-old', '+new'].join('\n')
    expect(mount(segment(data, { tool: 'edit_file', output: diff })).container.querySelector('[data-slot="code-diff"]')).not.toBeNull()
    expect(mount(segment(data, { tool: 'shell', input: 'echo live', output: 'live\n' })).container.querySelector('[data-slot="terminal-block"]')).not.toBeNull()
  })

  it('preserves explicit JSON envelope contracts for a separately registered tool', () => {
    const data = envelope('weather-current')
    const seg = segment(data, { tool: 'weather_tool', output: JSON.stringify({ generative_ui: data }), input: undefined })
    expect(resolveToolUISpec(seg, { weather_tool: ['weather-current'] })).toMatchObject({ producer: 'tool:weather_tool' })
    expect(renderAuiResult(seg, { weather_tool: ['weather-current'] })).toBeTruthy()
    expect(resolveToolUISpec(seg, connectedUISpecContracts)).toBeNull()
  })
})
