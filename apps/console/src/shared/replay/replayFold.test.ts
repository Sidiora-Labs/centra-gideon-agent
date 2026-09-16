import { describe, it, expect } from 'vitest'
import {
  replayChat,
  replayRun,
  adjacentDuplicateTextCount,
  type ChatStep,
} from './replayFold'



const HAPPY_PATH: ChatStep[] = [
  { kind: 'flush', text: 'The' },
  { kind: 'flush', text: 'The answer' },
  { kind: 'flush', text: 'The answer is 42.' },
]

describe('replayChat — happy path', () => {
  it('coalesces a growing streamed reply into a single text segment', () => {
    const { segs } = replayChat(HAPPY_PATH)
    const texts = segs.filter((s) => s.kind === 'text')
    expect(texts).toHaveLength(1)
    expect(texts[0]).toMatchObject({ kind: 'text', text: 'The answer is 42.' })
    expect(adjacentDuplicateTextCount(segs)).toBe(0)
  })
})


const WITH_ACTIVITY: ChatStep[] = [
  { kind: 'flush', text: 'Answer' },
  { kind: 'activity', text: 'recalled 3 memories', activityKind: 'memory' },
  { kind: 'flush', text: 'Answer complete.' },
]

describe('replayChat — activity insertion (K42)', () => {
  it('inserts the activity BEFORE the active text run so the flush replaces in place', () => {
    const { segs } = replayChat(WITH_ACTIVITY)
    expect(segs.map((s) => s.kind)).toEqual(['activity', 'text'])
    const text = segs.find((s) => s.kind === 'text')
    expect(text).toMatchObject({ text: 'Answer complete.' })
    expect(adjacentDuplicateTextCount(segs)).toBe(0)
  })
})


describe('replayChat — K44 duplicate detection', () => {
  it('a correct coalescer produces zero adjacent duplicate text segments', () => {
    const { segs } = replayChat(HAPPY_PATH)
    expect(adjacentDuplicateTextCount(segs)).toBe(0)
  })

  it('catches the K44 signature when a boundary wrongly splits a continuing run', () => {
    const K44_TRACE: ChatStep[] = [
      { kind: 'flush', text: 'The answer is 42.' },
      { kind: 'boundary' },
      { kind: 'flush', text: 'The answer is 42.' },
    ]
    const { segs } = replayChat(K44_TRACE)
    expect(adjacentDuplicateTextCount(segs)).toBe(1)
  })
})


describe('replayRun — lifecycle fold', () => {
  it('a gate failure then a passing re-run clears the banner', () => {
    const flags = replayRun([
      { event: 'gate_check', data: { ok: false, label: 'lint', command: 'make lint', output: 'x' } },
      { event: 'gate_check', data: { ok: true } },
    ])
    expect(flags.gate).toBeNull()
  })

  it('a stall then a cycle verdict clears the stall + judge-degraded flags', () => {
    const flags = replayRun([
      { event: 'judge_error' },
      { event: 'stage_stalled', data: { stage: 'build', findings: 2 } },
      { event: 'cycle_verdict' },
    ])
    expect(flags.stall).toBeNull()
    expect(flags.judgeDegraded).toBe(false)
  })

  it('deleted is terminal and sticks', () => {
    const flags = replayRun([{ event: 'deleted' }, { event: 'new_finding' }])
    expect(flags.deleted).toBe(true)
  })
})
