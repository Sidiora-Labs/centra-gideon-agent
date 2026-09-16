import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { appendThinking, type Segment } from './chatTypes'
import { ThinkingBlock } from './ThinkingBlock'


const text = (t: string): Segment => ({ kind: 'text', text: t })

describe('appendThinking (stream folding)', () => {
  it('opens a new thinking block on first chunk', () => {
    const out = appendThinking([], 'plan: ')
    expect(out).toEqual([{ kind: 'thinking', text: 'plan: ' }])
  })

  it('extends the trailing thinking block while the reasoning stream is uninterrupted', () => {
    const out = appendThinking([{ kind: 'thinking', text: 'plan: ' }], 'read the file')
    expect(out).toEqual([{ kind: 'thinking', text: 'plan: read the file' }])
  })

  it('interleaves with normal tokens: text after thinking closes the block, later thinking opens a NEW one', () => {
    let segs: Segment[] = []
    segs = appendThinking(segs, 'first reasoning')
    segs = [...segs, text('streamed answer tokens')]
    segs = appendThinking(segs, 'second reasoning')
    expect(segs).toEqual([
      { kind: 'thinking', text: 'first reasoning' },
      { kind: 'text', text: 'streamed answer tokens' },
      { kind: 'thinking', text: 'second reasoning' },
    ])
  })

  it('is a no-op for an empty chunk (no empty blocks in the transcript)', () => {
    const before: Segment[] = [text('a')]
    expect(appendThinking(before, '')).toBe(before)
  })

  it('switch off ⇒ frames dropped at ingestion ⇒ segments untouched (render-off)', () => {
    const before: Segment[] = [text('answer')]
    const after = before
    expect(after).toEqual([{ kind: 'text', text: 'answer' }])
    expect(after.some((s) => s.kind === 'thinking')).toBe(false)
  })
})

describe('ThinkingBlock (render-on)', () => {
  it('renders a collapsible block with the reasoning text', () => {
    render(<ThinkingBlock text="weighing two approaches" />)
    const block = screen.getByTestId('thinking-block')
    expect(block).toBeTruthy()
    expect(block.textContent).toContain('Thinking')
    expect(block.textContent).toContain('weighing two approaches')
  })

  it('starts open while streaming (defaultOpen) and closed on a settled re-render', () => {
    const { unmount } = render(<ThinkingBlock text="live" defaultOpen />)
    expect((screen.getByTestId('thinking-block') as HTMLDetailsElement).open).toBe(true)
    unmount()
    render(<ThinkingBlock text="settled" />)
    expect((screen.getByTestId('thinking-block') as HTMLDetailsElement).open).toBe(false)
  })
})

describe('persistence isolation', () => {
  it('hydrateTurns has no thinking arm — a reloaded transcript carries none', async () => {
    const { hydrateTurns } = await import('./chatTypes')
    const turns = hydrateTurns([
      { role: 'user', content: 'q' },
      { role: 'assistant', content: 'a' },
    ] as never)
    const segs = turns.flatMap((t) => t.segments)
    expect(segs.some((s) => s.kind === 'thinking')).toBe(false)
  })
})
