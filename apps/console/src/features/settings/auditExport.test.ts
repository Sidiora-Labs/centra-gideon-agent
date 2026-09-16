import { describe, expect, it } from 'vitest'
import { toJsonl } from './AuditPanel'
import type { SelEvent } from '../../shared/data/api'


const row = (over: Partial<SelEvent> = {}): SelEvent => ({
  event_id: 'e1', timestamp: '2026-08-16T12:00:00+00:00', event_type: 'tool_invocation',
  caller_identity: 'dashboard:abc', operation: 'execute_bash', outcome: 'completed',
  resources: 'ls -la', integrity_ok: true, ...over,
})

describe('toJsonl', () => {
  it('emits exactly one line per event', () => {
    const out = toJsonl([row({ event_id: 'a' }), row({ event_id: 'b' }), row({ event_id: 'c' })])
    expect(out.trim().split('\n')).toHaveLength(3)
  })

  it('round-trips: every line parses back to the original record', () => {
    const rows = [row({ event_id: 'a' }), row({ event_id: 'b', resources: 'has "quotes" and \\ slashes' })]
    const parsed = toJsonl(rows).trim().split('\n').map((l) => JSON.parse(l))
    expect(parsed).toEqual(rows)
  })

  it('survives a newline inside a field — the shape that would corrupt the line count', () => {
    const rows = [row({ resources: 'line one\nline two' }), row({ event_id: 'b' })]
    const out = toJsonl(rows)
    expect(out.trim().split('\n')).toHaveLength(2)
    expect(JSON.parse(out.trim().split('\n')[0]).resources).toBe('line one\nline two')
  })

  it('is newline-terminated so the file appends cleanly, and empty stays empty', () => {
    expect(toJsonl([row()]).endsWith('\n')).toBe(true)
    expect(toJsonl([])).toBe('')
  })

  it('carries the per-row integrity verdict into the export', () => {
    const parsed = JSON.parse(toJsonl([row({ integrity_ok: false })]).trim())
    expect(parsed.integrity_ok).toBe(false)
  })

  it('is a pure serializer — it does not invent a second redaction pass', () => {
    const redacted = '[REDACTED: credential]'
    expect(JSON.parse(toJsonl([row({ resources: redacted })]).trim()).resources).toBe(redacted)
  })
})
