import { describe, expect, it } from 'vitest'
import type { ToolSegment } from './chatTypes'
import { renderAuiResult, resolveToolUISpec } from './auiResultRegistry'
import { boundRecord } from '../../shared/ui/assistant-ui/qualification/generativeLiveData'
import { resolveAppUISpec } from '../../app/shell/appGenUiLayer'
import { render, screen } from '@testing-library/react'

const record = boundRecord('weather-current')
const contracts = { weather_tool: ['weather-current'] as const }
function segment(over: Partial<ToolSegment> = {}): ToolSegment {
  return { kind: 'tool', id: 'tool-call-42', tool: 'weather_tool', done: true, ok: true,
    output: JSON.stringify({ generative_ui: { schemaVersion: 1, template: record.template,
      recordId: record.recordId, bindings: record.bindings } }), ...over }
}

describe('typed real tool-result registration', () => {
  it('derives producer from the actual tool segment and preserves record identity', () => {
    const spec = resolveToolUISpec(segment(), contracts)
    expect(spec).toMatchObject({ template: 'weather-current', producer: 'tool:weather_tool', recordId: record.recordId })
    expect(renderAuiResult(segment(), contracts)).toBeTruthy()
  })

  it('mounts a completed read-only tool result through the donor renderer', () => {
    render(<>{renderAuiResult(segment(), contracts)}</>)
    expect(screen.getByRole('region', { name: 'Live weather-current result' })).toBeTruthy()
    expect(screen.getByText('Live children.2.value')).toBeTruthy()
  })

  it('requires an explicitly registered real tool and matching template', () => {
    expect(resolveToolUISpec(segment(), {})).toBeNull()
    expect(resolveToolUISpec(segment({ tool: 'unregistered_tool' }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment(), { weather_tool: ['stays'] })).toBeNull()
    expect(renderAuiResult(segment(), {})).toBeNull()
  })

  it('will not render incomplete, failed, truncated or oversized tool output', () => {
    expect(resolveToolUISpec(segment({ done: false }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ ok: false }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ truncated: true }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ output: '' }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ output: 'x'.repeat(1_000_001) }), contracts)).toBeNull()
  })

  it('requires a v1 live binding envelope and rejects stale or malformed data', () => {
    expect(resolveToolUISpec(segment({ output: 'not json' }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ output: '[]' }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ output: '{}' }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ output: '{"generative_ui":null}' }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ output: JSON.stringify({ generative_ui: { schemaVersion: 2,
      template: 'weather-current', recordId: record.recordId, bindings: record.bindings } }) }), contracts)).toBeNull()
    expect(resolveToolUISpec(segment({ output: JSON.stringify({ generative_ui: { schemaVersion: 1,
      template: 'weather-current', recordId: record.recordId, bindings: {} } }) }), contracts)).toBeNull()
  })

  it('keeps the app UISpec path separate from the existing GenUiWidget DSL', () => {
    const app = { name: 'calendar-app', enabled: true, uiComponents: 'renderer.mjs', uiCapabilities: ['generative-component'] }
    expect(resolveAppUISpec(app, { schemaVersion: 1, ...record })?.producer).toBe('app:calendar-app')
    expect(resolveAppUISpec(app, 'Table | rows=4')).toBeNull()
    expect(resolveAppUISpec(app, null)).toBeNull()
    expect(resolveAppUISpec(app, [])).toBeNull()
    expect(resolveAppUISpec({ ...app, enabled: false }, { schemaVersion: 1, ...record })).toBeNull()
    expect(resolveAppUISpec(app, { schemaVersion: 2, ...record })).toBeNull()
    window.location.hash = '#/dashboard?safe=1'
    expect(resolveAppUISpec(app, { schemaVersion: 1, ...record })).toBeNull()
    window.location.hash = '#/dashboard'
  })
})
