import { describe, it, expect } from 'vitest'
import { partitionRuns } from './scheduleFold'

type Row = { id?: string; outcome?: string }

function defaultView(schedule: Row[], didIds: string[]): Row[] {
  return partitionRuns(schedule, didIds).did
}

function expandedView(schedule: Row[], didIds: string[]): Row[] {
  const { did, suppressed } = partitionRuns(schedule, didIds)
  return [...did, ...suppressed]
}

const quietHours = (): Row[] =>
  Array.from({ length: 12 }, (_, i) =>
    i === 7 ? { id: 'REAL', outcome: 'ran' } : { id: `s${i}`, outcome: 'skipped_gate' },
  )

describe('the archive fold in the Schedule widget', () => {
  it('surfaces the one fire that RAN, even buried at index 7', () => {
    const visible = defaultView(quietHours(), ['REAL']).slice(0, 6)
    expect(visible.map((r) => r.id)).toEqual(['REAL'])
  })

  it('archives the suppressed rows out of the default view', () => {
    expect(defaultView(quietHours(), ['REAL'])).toHaveLength(1)
  })

  it('does NOT drop the suppressed rows — they reveal on demand', () => {
    const all = expandedView(quietHours(), ['REAL'])
    expect(all).toHaveLength(12)
    expect(all.filter((r) => r.outcome === 'skipped_gate')).toHaveLength(11)
  })

  it('keeps every real fire ahead of every suppression when expanded', () => {
    const rows: Row[] = [
      { id: 'a', outcome: 'skipped_gate' },
      { id: 'b', outcome: 'ran' },
      { id: 'c', outcome: 'skipped_budget' },
      { id: 'd', outcome: 'failed' },
    ]
    const ids = expandedView(rows, ['b', 'd']).map((r) => r.id)
    expect(ids).toEqual(['b', 'd', 'a', 'c'])
  })

  it('renders a LEGACY row with no id as work rather than hiding it', () => {
    const legacy: Row[] = [{ outcome: 'success' }, { outcome: 'failure' }]
    expect(defaultView(legacy, [])).toHaveLength(2)
    expect(defaultView(legacy, []).map((r) => r.outcome)).toEqual(['success', 'failure'])
  })

  it('shows everything in the default view when the server reports nothing suppressed', () => {
    const rows: Row[] = [{ id: 'x', outcome: 'ran' }, { id: 'y', outcome: 'ran' }]
    expect(defaultView(rows, ['x', 'y']).map((r) => r.id)).toEqual(['x', 'y'])
  })

  it('keys on `id`, not `run_id` — a FireRecord has no run_id', () => {
    const rows = [{ id: 'kept', run_id: '', outcome: 'ran' }, { id: 'gate', run_id: '', outcome: 'skipped_gate' }]
    expect(partitionRuns(rows, ['kept']).did.map((r) => r.id)).toEqual(['kept'])
    expect(partitionRuns(rows, ['kept']).suppressed.map((r) => r.id)).toEqual(['gate'])
  })
})
