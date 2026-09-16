import { describe, it, expect } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { CoalescerCore, useStreamCoalescer, MIN_BUDGET, MAX_BUDGET, MAX_LAG } from './useStreamCoalescer'
import { applyCoalescedFlush } from './coalesceReducers'
import type { Segment } from './chatTypes'

describe('CoalescerCore — accumulation + reveal', () => {
  it('accumulates pushes and reveals a growing prefix, never overshooting', () => {
    const c = new CoalescerCore()
    c.push('hello ')
    c.push('world')
    expect(c.backlog()).toBe(11)
    const r1 = c.tick(1)
    expect('hello world'.startsWith(r1)).toBe(true)
    expect(r1.length).toBeGreaterThan(0)
    expect(r1.length).toBeLessThanOrEqual(11)
  })

  it('fully reveals within a few ticks for a small backlog', () => {
    const c = new CoalescerCore()
    c.push('abcdefghij')
    let out = ''
    for (let i = 0; i < 10 && c.backlog() > 0; i++) out = c.tick(1)
    expect(out).toBe('abcdefghij')
    expect(c.backlog()).toBe(0)
  })

  it('never reveals more than what was pushed', () => {
    const c = new CoalescerCore()
    c.push('xyz')
    for (let i = 0; i < 50; i++) c.tick(1)
    expect(c.revealedText()).toBe('xyz')
    expect(c.revealedText().length).toBeLessThanOrEqual(3)
  })

  it('keeps revealing correctly when chunks arrive mid-drain', () => {
    const c = new CoalescerCore()
    c.push('first')
    c.tick(1)
    c.push('second')
    let out = ''
    for (let i = 0; i < 20 && c.backlog() > 0; i++) out = c.tick(1)
    expect(out).toBe('firstsecond')
  })
})

describe('CoalescerCore — adaptive budget', () => {
  it('respects the per-frame budget ceiling on a huge paste', () => {
    const c = new CoalescerCore()
    c.push('a'.repeat(10_000))
    const before = 0
    const r1 = c.tick(1)
    expect(r1.length - before).toBeLessThanOrEqual(MAX_BUDGET)
    expect(r1.length).toBeGreaterThanOrEqual(MIN_BUDGET)
  })

  it('drains a large backlog faster than a small one (drain-factor ramp)', () => {
    const big = new CoalescerCore(); big.push('a'.repeat(MAX_LAG * 3))
    const small = new CoalescerCore(); small.push('a'.repeat(50))
    big.tick(1); big.tick(1)
    small.tick(1); small.tick(1)
    const bigStep = big.revealedText().length
    const bigNext = big.tick(1).length - bigStep
    expect(bigNext).toBeGreaterThan(0)
    expect(bigNext).toBeLessThanOrEqual(MAX_BUDGET)
  })

  it('animSpeed scales the pace — higher speed reveals more per tick', () => {
    const slow = new CoalescerCore(); slow.push('a'.repeat(120))
    const fast = new CoalescerCore(); fast.push('a'.repeat(120))
    slow.tick(0.5)
    fast.tick(4)
    expect(fast.revealedText().length).toBeGreaterThan(slow.revealedText().length)
  })
})

describe('CoalescerCore — word-boundary snapping (CHAT-CRAFT S3)', () => {
  it('reveals whole words — never splits a word once the budget can reach a boundary', () => {
    const full = 'the quick brown fox jumps over'
    const isWs = (ch: string) => ch === ' '
    const c = new CoalescerCore()
    c.push(full)
    c.tick(1); c.tick(1)
    for (let i = 0; i < 40 && c.backlog() > 0; i++) {
      const out = c.tick(1)
      if (c.backlog() === 0) break
      const endsClean = out === '' || isWs(out[out.length - 1]) || isWs(full[out.length])
      expect(endsClean).toBe(true)
    }
    for (let i = 0; i < 40 && c.backlog() > 0; i++) c.tick(1)
    expect(c.revealedText()).toBe(full)
  })

  it('catch-up overrides snapping — a huge backlog reveals at the budget ceiling, unsnapped', () => {
    const c = new CoalescerCore()
    const words = ('word '.repeat(1000)).trim()
    c.push(words)
    c.tick(1); c.tick(1)
    const before = c.revealedText().length
    const after = c.tick(1).length
    expect(after - before).toBeGreaterThan(0)
    expect(after - before).toBeLessThanOrEqual(MAX_BUDGET)
  })

  it('never stalls — always makes forward progress even with no boundary in the window', () => {
    const c = new CoalescerCore()
    c.push('a'.repeat(200))
    const r1 = c.tick(1)
    expect(r1.length).toBeGreaterThanOrEqual(MIN_BUDGET)
  })

  it('snaps at CJK boundaries (each glyph is a word)', () => {
    const c = new CoalescerCore()
    c.push('你好世界这是一个测试')
    let out = ''
    for (let i = 0; i < 40 && c.backlog() > 0; i++) out = c.tick(1)
    expect(out).toBe('你好世界这是一个测试')
  })
})

describe('CoalescerCore — drainAll + reset', () => {
  it('drainAll reveals everything at once', () => {
    const c = new CoalescerCore()
    c.push('the quick brown fox')
    expect(c.drainAll()).toBe('the quick brown fox')
    expect(c.backlog()).toBe(0)
    expect(c.revealedText()).toBe('the quick brown fox')
  })

  it('reset clears pending + revealed + rate state', () => {
    const c = new CoalescerCore()
    c.push('stuff'); c.tick(1)
    c.reset()
    expect(c.backlog()).toBe(0)
    expect(c.revealedText()).toBe('')
    c.push('new')
    expect(c.backlog()).toBe(3)
  })

  it('tick on an empty core is a no-op (no throw, empty string)', () => {
    const c = new CoalescerCore()
    expect(c.tick(1)).toBe('')
    expect(c.backlog()).toBe(0)
  })
})

describe('stream boundaries consume each revealed prefix once', () => {
  const preamble = '22K files — substantial. Let me map it out.'

  it.each([true, false])('does not replay the preamble over consecutive tools (immediate=%s)', (immediate) => {
    let segments: Segment[] = []
    let coalescing = false
    const { result } = renderHook(() => useStreamCoalescer((revealed) => {
      const update = applyCoalescedFlush(segments, revealed, coalescing)
      segments = update.segs
      coalescing = update.coalescing
    }, { immediate }))

    act(() => {
      result.current.push(preamble)
      for (let index = 0; index < 30; index++) {
        result.current.flushNow()
        segments.push({ kind: 'tool', id: `call-${index}`, tool: 'bash', done: false })
        result.current.flushNow() // tool_call input update, with no intervening chat_chunk
      }
      result.current.flushNow() // chat_segment
      result.current.flushNow() // chat_done
    })

    expect(segments.filter((segment) => segment.kind === 'text')).toEqual([{ kind: 'text', text: preamble }])
    expect(segments.filter((segment) => segment.kind === 'tool')).toHaveLength(30)
  })

  it('preserves equal text in distinct runs and repeated words within a run', () => {
    const emissions: string[] = []
    const { result } = renderHook(() => useStreamCoalescer((text) => emissions.push(text), { immediate: true }))
    act(() => {
      result.current.push(preamble)
      result.current.flushNow()
      result.current.reset()
      result.current.push(preamble)
      result.current.push(preamble)
      result.current.flushNow()
    })
    expect(emissions).toEqual([preamble, preamble, preamble + preamble])
  })

  it('does not create empty prose when a turn only emits tools', () => {
    const emissions: string[] = []
    const { result } = renderHook(() => useStreamCoalescer((text) => emissions.push(text)))
    act(() => {
      result.current.flushNow()
      result.current.flushNow()
      result.current.reset()
      result.current.flushNow()
    })
    expect(emissions).toEqual([])
  })

  it('emits the remaining animated tail once when a boundary drains it', () => {
    const core = new CoalescerCore()
    core.push(preamble)
    const firstFrame = core.tick(1)
    expect(firstFrame.length).toBeLessThan(preamble.length)
    expect(core.takeRevealed()).toBe(firstFrame)
    expect(core.takeRevealed()).toBeNull()
    core.drainAll()
    expect(core.takeRevealed()).toBe(preamble)
    core.drainAll()
    expect(core.takeRevealed()).toBeNull()
  })
})
