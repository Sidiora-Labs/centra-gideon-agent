import { describe, expect, it } from 'vitest'
import { WORKFLOW_BATCH_EVENT, WORKFLOW_LIFECYCLE, unwrapBatch } from './useWorkflowStream'
import { foldEvent, foldEvents, foldSnapshot } from './workflowFold'
import type { WorkflowRunDetailData } from '../../shared/data/api'
import realFrames from './__fixtures__/realBatchFrames.json'


const snap = (over: Partial<WorkflowRunDetailData> = {}): WorkflowRunDetailData => ({
  run_id: 'a1b2c3d4',
  workflow: 'triage',
  status: 'running',
  spec_version: 1,
  error: '',
  attention: null,
  tokens: 0,
  elapsed_secs: 0,
  nodes: [
    { instance_path: 'root.children[0]', node_id: 'gather', state: 'pending' },
    { instance_path: 'root.children[1]', node_id: 'analyze', state: 'pending' },
  ],
  ...over,
})

const member = (event: string, payload: Record<string, unknown>) => ({ event, payload })

describe('unwrapBatch', () => {
  it('returns the members in wire order', () => {
    const out = unwrapBatch({
      events: [
        member('workflow_node_started', { instance_path: 'a', seq: 1 }),
        member('workflow_node_done', { instance_path: 'a', seq: 2 }),
      ],
    })
    expect(out.map((m) => m.event)).toEqual(['workflow_node_started', 'workflow_node_done'])
    expect(out.map((m) => (m.data as { seq: number }).seq)).toEqual([1, 2])
  })

  it('drops an event the FE union does not know', () => {
    const out = unwrapBatch({
      events: [
        member('workflow_node_done', { instance_path: 'a' }),
        member('workflow_from_a_future_slice', { instance_path: 'b' }),
      ],
    })
    expect(out).toHaveLength(1)
    expect(out[0].event).toBe('workflow_node_done')
  })

  it('tolerates a malformed frame instead of throwing', () => {
    expect(unwrapBatch(null)).toEqual([])
    expect(unwrapBatch({})).toEqual([])
    expect(unwrapBatch({ events: 'not an array' })).toEqual([])
    expect(unwrapBatch({ events: [null, 42, { noEvent: true }] })).toEqual([])
  })

  it('preserves each member envelope so the fold guards still work', () => {
    const out = unwrapBatch({
      events: [member('workflow_node_done', { instance_path: 'a', event_id: 'e1', seq: 7, epoch: 2 })],
    })
    expect(out[0].data).toMatchObject({ event_id: 'e1', seq: 7, epoch: 2 })
  })

  it('the batch event name is distinct from every lifecycle event', () => {
    expect(WORKFLOW_LIFECYCLE).not.toContain(WORKFLOW_BATCH_EVENT)
  })
})

describe('batched and unbatched folds converge', () => {
  const events = [
    { event: 'workflow_node_started' as const, data: { run_id: 'a1b2c3d4', event_id: 'e1', seq: 1, epoch: 0, instance_path: 'root.children[0]', node_id: 'gather' } },
    { event: 'workflow_node_done' as const, data: { run_id: 'a1b2c3d4', event_id: 'e2', seq: 2, epoch: 0, instance_path: 'root.children[0]', node_id: 'gather', status: 'done' } },
    { event: 'workflow_node_started' as const, data: { run_id: 'a1b2c3d4', event_id: 'e3', seq: 3, epoch: 0, instance_path: 'root.children[1]', node_id: 'analyze' } },
    { event: 'workflow_node_done' as const, data: { run_id: 'a1b2c3d4', event_id: 'e4', seq: 4, epoch: 0, instance_path: 'root.children[1]', node_id: 'analyze', status: 'done' } },
  ]

  it('folding a batch equals folding the same events individually', () => {
    const individually = foldEvents(foldSnapshot(snap()), events)
    const batched = foldEvents(
      foldSnapshot(snap()),
      unwrapBatch({ events: events.map((e) => member(e.event, e.data)) }),
    )
    expect(batched.nodes).toEqual(individually.nodes)
    expect(batched.doneCount).toBe(individually.doneCount)
    expect(batched.progress).toBe(individually.progress)
    expect(batched.dropped).toBe(individually.dropped)
  })

  it('a re-delivered batch is idempotent, exactly as re-delivered frames are', () => {
    const first = foldEvents(foldSnapshot(snap()), unwrapBatch({ events: events.map((e) => member(e.event, e.data)) }))
    const again = foldEvents(first, unwrapBatch({ events: events.map((e) => member(e.event, e.data)) }))
    expect(again.nodes).toEqual(first.nodes)
    expect(again.doneCount).toBe(first.doneCount)
  })

  it('a batch after a rewind is still epoch-dropped member by member', () => {
    let vm = foldSnapshot(snap())
    vm = foldEvent(vm, 'workflow_run_update', { run_id: 'a1b2c3d4', event_id: 'r1', epoch: 5, status: 'running' })
    const stale = foldEvents(vm, unwrapBatch({
      events: [
        member('workflow_node_done', { run_id: 'a1b2c3d4', event_id: 'e9', seq: 9, epoch: 1, instance_path: 'root.children[0]', status: 'done' }),
        member('workflow_node_done', { run_id: 'a1b2c3d4', event_id: 'e10', seq: 10, epoch: 5, instance_path: 'root.children[1]', status: 'done' }),
      ],
    }))
    expect(stale.doneCount).toBe(1)
    expect(stale.dropped).toBe(1)
    expect(stale.nodes.find((n) => n.node_id === 'analyze')?.state).toBe('done')
  })
})

describe('real captured batch frames', () => {
  const fixture = realFrames as {
    snapshot: WorkflowRunDetailData
    frames: Array<{ event: string; data: unknown }>
  }

  const replay = () => {
    let vm = foldSnapshot(fixture.snapshot)
    for (const f of fixture.frames) {
      if (f.event === WORKFLOW_BATCH_EVENT) vm = foldEvents(vm, unwrapBatch(f.data))
      else vm = foldEvent(vm, f.event as (typeof WORKFLOW_LIFECYCLE)[number], f.data)
    }
    return vm
  }

  it('the capture really is coalesced — 26 events in 3 frames', () => {
    expect(fixture.frames).toHaveLength(3)
    const batch = fixture.frames.find((f) => f.event === WORKFLOW_BATCH_EVENT)
    expect(unwrapBatch(batch?.data)).toHaveLength(24)
  })

  it('folding the real frames reconstructs a complete 12-node run', () => {
    const vm = replay()
    expect(vm.status).toBe('complete')
    expect(vm.doneCount).toBe(12)
    expect(vm.totalCount).toBe(12)
    expect(vm.progress).toBe(1)
    expect(vm.live).toBe(false)
  })

  it('every real member carried a full envelope', () => {
    const members = unwrapBatch(fixture.frames.find((f) => f.event === WORKFLOW_BATCH_EVENT)?.data)
    for (const m of members) {
      const p = m.data as Record<string, unknown>
      expect(typeof p.event_id).toBe('string')
      expect(typeof p.seq).toBe('number')
      expect(typeof p.epoch).toBe('number')
      expect(typeof p.node_epoch).toBe('number')
    }
    const seqs = members.map((m) => (m.data as { seq: number }).seq)
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b))
  })

  it('replaying the real capture twice changes nothing', () => {
    const once = replay()
    let twice = once
    for (const f of fixture.frames) {
      twice = f.event === WORKFLOW_BATCH_EVENT
        ? foldEvents(twice, unwrapBatch(f.data))
        : foldEvent(twice, f.event as (typeof WORKFLOW_LIFECYCLE)[number], f.data)
    }
    expect(twice.nodes).toEqual(once.nodes)
    expect(twice.doneCount).toBe(once.doneCount)
  })
})
