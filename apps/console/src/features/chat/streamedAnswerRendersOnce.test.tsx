import { describe, expect, it } from 'vitest'
import { CoalescerCore, type StreamCursor } from './useStreamCoalescer'
import { normalizeCompletedSnapshot, SnapshotReplay } from './snapshotReplay'
import { hydrateTurns, turnText, type HistMsg } from './chatTypes'

const first: StreamCursor = { stream_epoch: 'epoch', stream_turn: 2, stream_seq: 4 }

describe('stream cursor coalescing', () => {
  it('resumes from the visible partial and accepts only newer chunks', () => {
    const core = new CoalescerCore()
    core.resume('one, two, ', first)
    expect(core.push('duplicate', first)).toBe(false)
    expect(core.push('older', { ...first, stream_seq: 3 })).toBe(false)
    expect(core.push('three', { ...first, stream_seq: 5 })).toBe(true)
    expect(core.drainAll()).toBe('one, two, three')
    expect(core.takeRevealed()).toBe('one, two, three')
  })

  it('keeps the watermark across text boundaries and replaces it from a late snapshot', () => {
    const core = new CoalescerCore()
    expect(core.push('first', first)).toBe(true)
    core.reset()
    expect(core.push('duplicate', first)).toBe(false)
    core.resume('one, ', { ...first, stream_seq: 1 })
    expect(core.push('two, ', { ...first, stream_seq: 2 })).toBe(true)
    expect(core.push('three', { ...first, stream_seq: 3 })).toBe(true)
    expect(core.drainAll()).toBe('one, two, three')
  })

  it('admits unstamped chunks and a newer stream epoch', () => {
    const core = new CoalescerCore()
    core.resume(null, first)
    expect(core.push('unstamped')).toBe(true)
    expect(core.push('new stream', { stream_epoch: 'next', stream_turn: 0, stream_seq: 0 })).toBe(true)
    expect(core.drainAll()).toBe('unstampednew stream')
  })
})

describe('snapshot replay around overlapping detail reads', () => {
  it('replaces only a cursor-matched terminal stream projection before transcript hydration', () => {
    const cursor: StreamCursor = { stream_epoch: 'epoch', stream_turn: 2, stream_seq: 3 }
    const cursorMeta = { stream_epoch: cursor.stream_epoch, stream_turn: cursor.stream_turn, stream_seq: cursor.stream_seq }
    const messages: HistMsg[] = [
      { role: 'user', content: 'earlier' },
      { role: 'streaming', content: 'older unwatermarked projection' },
      { role: 'user', content: 'count to three' },
      { role: 'streaming', content: 'one, two, three', meta: cursorMeta as unknown as HistMsg['meta'] },
      { role: 'assistant', content: 'one, two, three', meta: cursorMeta as unknown as HistMsg['meta'] },
    ]
    const detail = normalizeCompletedSnapshot({ running: false, stream_cursor: cursor, messages })
    expect(detail.messages.map((message) => message.role)).toEqual(['user', 'streaming', 'user', 'assistant'])
    const turns = hydrateTurns(detail.messages, detail.running)
    expect(turnText(turns[turns.length - 1])).toBe('one, two, three')
  })

  it('holds frames until the adopted snapshot settles, then returns them once in order', () => {
    const replay = new SnapshotReplay<string>(() => true)
    const read = replay.begin()
    expect(replay.hold('chunk-a')).toBe(true)
    expect(replay.hold('chunk-b')).toBe(true)
    expect(replay.settle(read, () => true)).toEqual(['chunk-a', 'chunk-b'])
    expect(replay.busy()).toBe(false)
    expect(replay.hold('chunk-c')).toBe(false)
  })

  it('does not let an older read replace the newer snapshot', () => {
    const replay = new SnapshotReplay<string>(() => true)
    const older = replay.begin()
    replay.hold('before-newer-read')
    const newer = replay.begin()
    replay.hold('after-newer-read')
    expect(replay.settle(newer, () => true)).toEqual(['before-newer-read', 'after-newer-read'])
    let adoptedOlder = false
    expect(replay.settle(older, () => { adoptedOlder = true; return true })).toEqual([])
    expect(adoptedOlder).toBe(false)
  })

  it('replays only transcript effects erased by a released read', () => {
    const replay = new SnapshotReplay<string>(frame => frame !== 'external-effect')
    const read = replay.begin()
    expect(replay.hold('chunk-before-release')).toBe(true)
    expect(replay.release(read)).toEqual(['chunk-before-release'])
    expect(replay.hold('chunk-after-release')).toBe(false)
    expect(replay.hold('external-effect')).toBe(false)
    expect(replay.settle(read, () => true)).toEqual(['chunk-before-release', 'chunk-after-release'])
  })

  it('returns held frames when the detail read fails', () => {
    const replay = new SnapshotReplay<string>(() => true)
    const read = replay.begin()
    replay.hold('chunk')
    expect(replay.settle(read, null)).toEqual(['chunk'])
  })
})
