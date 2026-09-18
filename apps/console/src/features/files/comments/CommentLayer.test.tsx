import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { useRef } from 'react'
import { CommentLayer } from './CommentLayer'
import { commentStore } from './commentStore'


function Host({ docId, docLabel }: { docId: string; docLabel: string }) {
  const scroll = useRef<HTMLDivElement>(null)
  return (
    <div>
      <div ref={scroll}>document body</div>
      <CommentLayer scrollRef={scroll} docId={docId} docLabel={docLabel} onSubmit={() => {}} />
    </div>
  )
}

const seed = (docId: string, docLabel: string, comment: string) =>
  commentStore.add({ docId, docLabel, quote: `quote for ${comment}`, comment })

const deck = () => {
  const button = screen.getByRole('button', { name: /comment/i })
  return button
}
const lead = () => {
  const el = document.querySelector<HTMLElement>('[data-testid="comment-deck-lead"]')
  if (!el) throw new Error('the collapsed deck no longer renders a lead comment')
  return el
}
const spread = () => document.querySelector<HTMLElement>('[data-testid="comment-deck-spread"]')

beforeEach(() => { commentStore.clear() })
afterEach(() => { commentStore.clear() })

describe('the collapsed comment deck speaks for the document you are looking at', () => {
  it('shows the open document\'s newest comment, unmuted, even when a newer one belongs elsewhere', () => {
    seed('a', 'Doc A', 'first on A')
    seed('a', 'Doc A', 'newest on A')
    seed('b', 'Doc B', 'newest overall, but on B')
    render(<Host docId="a" docLabel="Doc A" />)

    expect(within(lead()).getByText('newest on A')).toBeInTheDocument()
    expect(lead().textContent, 'the other document must not speak for this one').not.toContain('on B')
    expect(lead().className, 'the open document is never the muted case').not.toContain('opacity-65')
    expect(deck().textContent).toContain('Doc A')
  })

  it('still counts every comment, across documents, while showing only the open one', () => {
    seed('a', 'Doc A', 'on A')
    seed('b', 'Doc B', 'on B')
    render(<Host docId="a" docLabel="Doc A" />)
    expect(deck().textContent).toContain('2 comments')
    expect(spread()?.textContent, 'and it says the pile is not all from here').toContain('2 documents')
  })

  it('mutes the fallback and discloses the spread when the open document has none', () => {
    seed('a', 'Doc A', 'on A')
    seed('b', 'Doc B', 'newest, on B')
    render(<Host docId="c" docLabel="Doc C" />)

    expect(within(lead()).getByText('newest, on B')).toBeInTheDocument()
    expect(lead().className, 'a borrowed comment must read as borrowed').toContain('opacity-65')
    expect(deck().textContent, 'and it must name the document it came from').toContain('Doc B')
    expect(spread(), 'the collapsed deck must disclose that it is showing another document').toBeTruthy()
    expect(spread()!.textContent).toContain('2 documents')
  })

  it('discloses a single foreign document too — one other document is still not this one', () => {
    seed('b', 'Doc B', 'only on B')
    render(<Host docId="a" docLabel="Doc A" />)

    expect(lead().className).toContain('opacity-65')
    expect(spread()!.textContent).toContain('another document')
  })

  it('says nothing about other documents when there are none', () => {
    seed('a', 'Doc A', 'first on A')
    seed('a', 'Doc A', 'second on A')
    render(<Host docId="a" docLabel="Doc A" />)

    expect(within(lead()).getByText('second on A')).toBeInTheDocument()
    expect(lead().className).not.toContain('opacity-65')
    expect(spread(), 'a single-document deck must not cry cross-document').toBeNull()
  })

  it('renders no deck at all with no comments', () => {
    render(<Host docId="a" docLabel="Doc A" />)
    expect(document.querySelector('[data-testid="comment-deck-lead"]')).toBeNull()
  })
})
