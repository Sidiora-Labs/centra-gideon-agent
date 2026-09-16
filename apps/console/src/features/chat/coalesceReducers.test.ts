import { describe, it, expect } from 'vitest'
import { applyCoalescedFlush, insertActivity } from './coalesceReducers'
import type { Segment } from './chatTypes'


const text = (t: string): Segment => ({ kind: 'text', text: t })
const activity = (t: string): Segment => ({ kind: 'activity', text: t, activityKind: 'context' })
const tool = (): Segment => ({ kind: 'tool', id: 't1', tool: 'x', done: false } as Segment)

describe('applyCoalescedFlush — replace vs push', () => {
  it('first flush of a fresh run PUSHES a text segment + takes ownership', () => {
    const r = applyCoalescedFlush([], 'Hel', false)
    expect(r.segs).toEqual([text('Hel')])
    expect(r.coalescing).toBe(true)
  })

  it('subsequent flushes REPLACE the owned trailing text in place (no growth in count)', () => {
    let segs: Segment[] = []
    let coalescing = false
    ;({ segs, coalescing } = applyCoalescedFlush(segs, 'Hel', coalescing))
    ;({ segs, coalescing } = applyCoalescedFlush(segs, 'Hello', coalescing))
    ;({ segs, coalescing } = applyCoalescedFlush(segs, 'Hello world', coalescing))
    expect(segs).toEqual([text('Hello world')])
  })

  it('K44/K45: when coalescing is FALSE (fresh send/turn), flush PUSHES beside prior text — never replaces it', () => {
    const prior = [text('Turn 1 full answer.')]
    const r = applyCoalescedFlush(prior, 'Turn 2', false)
    expect(r.segs).toEqual([text('Turn 1 full answer.'), text('Turn 2')])
    expect(r.segs[0]).toEqual(text('Turn 1 full answer.'))
  })

  it('K42: after an activity segment interleaves, a still-coalescing flush does NOT duplicate — it replaces the tail only when the tail is text', () => {
    let segs: Segment[] = [text('The answer so far')]
    let coalescing = true
    segs = insertActivity(segs, 'recalled context', 'context', coalescing)

    expect(segs[segs.length - 1]).toEqual(text('The answer so far'))
    ;({ segs, coalescing } = applyCoalescedFlush(segs, 'The answer so far, extended', coalescing))
    expect(segs.filter((s) => s.kind === 'text')).toHaveLength(1)
    expect(segs.filter((s) => s.kind === 'activity')).toHaveLength(1)
    expect(segs[segs.length - 1]).toEqual(text('The answer so far, extended'))
  })
})

describe('insertActivity — K42 ordering discipline', () => {
  it('inserts BEFORE the active coalesced text run (keeps text as the tail)', () => {
    const segs = insertActivity([text('streaming answer')], 'recalled context', 'context', true)
    expect(segs).toEqual([activity('recalled context'), text('streaming answer')])
    expect(segs[segs.length - 1].kind).toBe('text')
  })

  it('appends at the end when NOT coalescing (turn done — no active run to protect)', () => {
    const segs = insertActivity([text('final answer')], 'telemetry', 'context', false)
    expect(segs).toEqual([text('final answer'), activity('telemetry')])
  })

  it('de-dupes an identical adjacent activity line (returns same array by identity)', () => {
    const start: Segment[] = [activity('recalled context'), text('answer')]
    const out = insertActivity(start, 'recalled context', 'context', true)
    expect(out).toBe(start)
  })

  it('tool cards win — activity is dropped when a tool segment is present', () => {
    const start: Segment[] = [tool()]
    expect(insertActivity(start, 'recalled context', 'context', true)).toBe(start)
  })
})
