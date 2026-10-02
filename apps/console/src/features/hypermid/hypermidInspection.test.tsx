import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { HypermidPrimaryContextInspectionWire } from '../../shared/data/api'
import { PrimaryContextEvidence } from './SessionInspector'
import {
  adoptTimelineSnapshot,
  beginConflictDraft,
  inspectionQueryKey,
  reapplyConflict,
  reloadConflict,
  retainRejectedDraft,
  type HypermidTimelineItem,
} from './hypermidState'

function item(sequence: number, summary: string, epoch = 4): HypermidTimelineItem {
  return {
    id: `${epoch}-${sequence}`,
    cursor: { epoch, sequence },
    kind: 'assistant',
    occurred_at: '2026-10-02T00:00:00Z',
    summary,
  }
}

describe('Hypermid inspection state', () => {
  it('adopts an authoritative snapshot and applies only newer same-epoch events once', () => {
    const result = adoptTimelineSnapshot({
      session_id: 'session-a',
      cursor: { epoch: 4, sequence: 2 },
      items: [item(2, 'terminal answer'), item(1, 'question')],
    }, [item(2, 'duplicate terminal answer'), item(3, 'maintenance receipt'), item(1, 'old replay'), item(4, 'wrong epoch', 3)])

    expect(result.items.map((row) => row.summary)).toEqual(['question', 'terminal answer', 'maintenance receipt'])
    expect(result.cursor).toEqual({ epoch: 4, sequence: 3 })
  })

  it('keeps complete and filtered collections under distinct deterministic keys', () => {
    expect(inspectionQueryKey('memory', {})).toBe('hypermid:memory:complete')
    expect(inspectionQueryKey('memory', { kind: 'anchor', q: 'launch' })).toBe(
      'hypermid:memory:filtered:kind=anchor&q=launch',
    )
    expect(inspectionQueryKey('memory', { q: 'launch', kind: 'anchor' })).toBe(
      'hypermid:memory:filtered:kind=anchor&q=launch',
    )
  })

  it('retains a refused draft and never pairs it with a fresh revision implicitly', () => {
    const opened = { ...beginConflictDraft('saved value', 'revision-a'), draft: 'my edit' }
    const refused = retainRejectedDraft(opened, 'new saved value', 'revision-b', 'Changed elsewhere')
    expect(refused.draft).toBe('my edit')
    expect(refused.revision).toBe('revision-a')

    expect(reloadConflict(refused)).toEqual(beginConflictDraft('new saved value', 'revision-b'))
    expect(reapplyConflict(refused)).toEqual({
      authoritative: 'new saved value',
      revision: 'revision-b',
      draft: 'my edit',
    })
  })

  it('renders the redacted primary context budget, summary, and digest evidence', () => {
    const inspection: HypermidPrimaryContextInspectionWire = {
      state: 'complete', writer: 'hypermid', writer_status: { module_id: 'context', epoch: 3, generation: 8 },
      scope: { owner_id: 'owner', project_id: 'project' }, observed_at: Date.parse('2026-10-02T12:00:00Z'),
      digest_health: { state: 'healthy', component_count: 4, mismatches: [], projection_digest: 'a'.repeat(64), serialized_digest: 'a'.repeat(64) },
      active_digest: 'b'.repeat(64),
      cache: { freshness: 'fresh', generation: 8, bytes: 4096, bytes_known: true, reason: 'applied', regions: [] },
      recall_arms: [{ arm: 'semantic', hits: 2 }],
      summary: { authorized_records: 7, selected_records: 2, memory_cursor: { epoch: 3, sequence: 5 } },
      provenance: [], components: [],
      budget_evidence: { projected_tokens: 800, assembled_tokens: 920, recall_tokens: 120, max_input_tokens: 2000, within_limit: true },
    }
    const html = renderToStaticMarkup(<PrimaryContextEvidence inspection={inspection} />)
    expect(html).toContain('Primary model context')
    expect(html).toContain('2 selected of 7 authorized')
    expect(html).toContain('920 of 2,000 tokens')
    expect(html).toContain(`Active digest ${'b'.repeat(64)}`)
    expect(html).not.toContain('semantic')
  })
})
