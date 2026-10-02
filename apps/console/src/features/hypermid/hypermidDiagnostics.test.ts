import { describe, expect, it } from 'vitest'
import type { HypermidDiagnosticsWire, HypermidLogPageWire } from '../../shared/data/api'
import { mergeLogPage, retainDiagnostics } from './diagnosticsState'

const scope = { owner_id: 'owner', project_id: 'project' }
const diagnostic = (observed_at: string): HypermidDiagnosticsWire => ({
  scope, observed_at, cursor: { epoch: 2, sequence: 4 }, cached: true, checks: [],
})
const page = (sequence: number, gap = false): HypermidLogPageWire => ({
  scope,
  cursor: { epoch: 2, sequence },
  gap,
  recovery_cursor: gap ? { epoch: 2, sequence: sequence - 1 } : null,
  entries: [{ cursor: { epoch: 2, sequence }, observed_at: '2026-10-02T12:00:00Z', severity: 'info', component: 'adapter', message: `entry ${sequence}`, fields: {} }],
})

describe('Hypermid diagnostic state', () => {
  it('retains the last observation when a rerun yields no replacement', () => {
    const current = diagnostic('2026-10-02T12:00:00Z')
    expect(retainDiagnostics(current, undefined)).toBe(current)
    expect(retainDiagnostics(current, diagnostic('2026-10-02T12:01:00Z'))?.observed_at).toBe('2026-10-02T12:01:00Z')
  })

  it('deduplicates resumed log pages and makes a retention gap durable', () => {
    const first = mergeLogPage({ entries: [], gap: false, recoveryCursor: null }, page(4))
    const repeated = mergeLogPage(first, page(4))
    expect(repeated.entries).toHaveLength(1)
    const recovered = mergeLogPage(repeated, page(8, true))
    expect(recovered.entries.map((entry) => entry.cursor.sequence)).toEqual([8])
    expect(recovered.gap).toBe(true)
    expect(recovered.recoveryCursor).toEqual({ epoch: 2, sequence: 7 })
  })
})
