import { describe, it, expect } from 'vitest'
import { findSegments } from './findSegments'
import { matchingIndices } from '../../shared/ui/findText'
import type { ChatTurn } from './chatTypes'


const userTurn = (text: string): ChatTurn => ({ role: 'user', segments: [{ kind: 'text', text }] })
const asstTurn = (...texts: string[]): ChatTurn => ({ role: 'assistant', segments: texts.map((t) => ({ kind: 'text' as const, text: t })) })
const toolTurn = (tool: string, detail?: string): ChatTurn => ({ role: 'assistant', segments: [{ kind: 'tool', id: 't', tool, detail, done: true }] })

describe('findSegments', () => {
  it('extracts text segments and tool titles in render order', () => {
    const t: ChatTurn = { role: 'assistant', segments: [
      { kind: 'text', text: 'before' },
      { kind: 'tool', id: 't1', tool: 'Terminal', detail: 'docker ps', done: true },
      { kind: 'text', text: 'after' },
    ] }
    expect(findSegments(t)).toEqual(['before', 'Terminal docker ps', 'after'])
  })

  it('keeps index alignment for non-searchable segments (approval → empty string)', () => {
    const t: ChatTurn = { role: 'assistant', segments: [
      { kind: 'approval', id: 'a', tool: 'Write' },
      { kind: 'text', text: 'hi' },
    ] }
    expect(findSegments(t)).toEqual(['', 'hi'])
  })
})

describe('the chat binding: matchingIndices over findSegments', () => {
  it('empty / whitespace query matches no turn', () => {
    const turns = [userTurn('hello world')]
    expect(matchingIndices(turns, findSegments, '')).toEqual([])
    expect(matchingIndices(turns, findSegments, '   ')).toEqual([])
  })

  it('scans turns in reading order and reports each matching turn ONCE', () => {
    const turns = [userTurn('cat'), asstTurn('a cat', 'cat again'), userTurn('dog'), toolTurn('Read', 'cat.txt')]
    expect(matchingIndices(turns, findSegments, 'cat')).toEqual([0, 1, 3])
  })

  it('is case-insensitive through the composition, not just in the matcher', () => {
    expect(matchingIndices([userTurn('Docker Compose')], findSegments, 'docker')).toEqual([0])
  })

  it('a query straddling two segments matches nothing — the seam is real', () => {
    const t = asstTurn('before', 'after')
    expect(matchingIndices([t], findSegments, 'before')).toEqual([0])
    expect(matchingIndices([t], findSegments, 'before after')).toEqual([])
  })

  it('is fast on a 500-turn fixture', () => {
    const turns: ChatTurn[] = Array.from({ length: 500 }, (_, i) =>
      asstTurn(`turn ${i} — the quick brown fox jumps over the lazy dog repeatedly `.repeat(6)))
    const t0 = performance.now()
    const idx = matchingIndices(turns, findSegments, 'fox')
    const dt = performance.now() - t0
    expect(idx.length).toBe(500)
    expect(dt).toBeLessThan(250)
  })
})
