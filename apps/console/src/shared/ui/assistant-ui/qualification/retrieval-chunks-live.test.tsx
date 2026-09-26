import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { RetrievalChunks, type RetrievalChunk } from '../../../vendor/assistant-ui/elements/retrieval-chunks'

const scored: RetrievalChunk = {
  id: 'knowledge-42',
  source: 'Operations runbook',
  locator: 'L40–L55',
  score: 0.91,
  text: 'Restart the worker after the queue drains.',
  tokens: 82,
  sourceType: 'document',
  section: 'Recovery',
  lineRange: [40, 55],
  deepLink: '/knowledge/knowledge-42',
}
const unscored: RetrievalChunk = {
  id: 'knowledge-77',
  source: 'Shift notes',
  locator: 'L8',
  text: 'The on-call agent verified delivery.',
  tokens: 23,
}

describe('RetrievalChunks live record rendering', () => {
  it('retains supplied score, passage, tokens, and source metadata', () => {
    const view = render(<RetrievalChunks query="worker recovery" chunks={[scored]} visibleCount={1} searching={false} />)
    const root = view.container.querySelector('[data-slot="retrieval-chunks"]') as HTMLElement
    expect(within(root).getByText('worker recovery')).toBeTruthy()
    expect(within(root).getByText('1 passages')).toBeTruthy()
    expect(within(root).getByText('Operations runbook')).toBeTruthy()
    expect(within(root).getByText('L40–L55')).toBeTruthy()
    expect(within(root).getByText('Restart the worker after the queue drains.')).toBeTruthy()
    expect(within(root).getByText('82 tokens')).toBeTruthy()
    expect(within(root).getByText('document')).toBeTruthy()
    expect(within(root).getByText('Recovery')).toBeTruthy()
    expect(within(root).getByText('Lines 40–55')).toBeTruthy()
    expect(within(root).getByText('0.91')).toHaveClass('text-emerald-600')
    const meter = within(root).getByRole('meter', { name: 'Operations runbook relevance score' })
    expect(meter).toHaveAttribute('aria-valuenow', '91')
    expect(meter).toHaveAttribute('aria-valuetext', '0.91 of 1.00')
    expect(meter.querySelector('[style]')).toHaveStyle({ width: '91%' })
  })

  it('does not fabricate a relevance score or an action for an unscored record', () => {
    const view = render(<RetrievalChunks query="delivery" chunks={[unscored]} visibleCount={1} searching={false} />)
    expect(screen.getByText('The on-call agent verified delivery.')).toBeTruthy()
    expect(screen.getByText('23 tokens')).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
    expect(view.container.querySelector('a[href]')).toBeNull()
    expect(screen.queryByText('0.00')).toBeNull()
    expect(screen.queryByText(/relevance unavailable/i)).toBeNull()
  })

  it.each([null, Number.NaN, -0.1, 1.1])('hides an absent or invalid score %s rather than showing a false meter', score => {
    const view = render(<RetrievalChunks query="delivery" chunks={[{ ...unscored, score }]} visibleCount={1} searching={false} />)
    expect(screen.queryByRole('meter')).toBeNull()
    expect(view.container.querySelector('[style*="width"]')).toBeNull()
    expect(screen.getByText('The on-call agent verified delivery.')).toBeTruthy()
  })

  it('treats a real zero score and zero tokens as recorded values', () => {
    render(<RetrievalChunks query="empty result" chunks={[{ ...unscored, score: 0, tokens: 0 }]} visibleCount={1} searching={false} />)
    expect(screen.getByText('0.00')).toBeTruthy()
    expect(screen.getByText('0 tokens')).toBeTruthy()
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '0')
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuetext', '0.00 of 1.00')
  })

  it('omits optional metadata when no source supplied it', () => {
    const minimal: RetrievalChunk = { id: 'knowledge-88', source: 'Record', locator: 'P2', text: 'Observed fact.' }
    const view = render(<RetrievalChunks query="fact" chunks={[minimal]} visibleCount={1} searching={false} />)
    expect(screen.getByText('Record')).toBeTruthy()
    expect(screen.getByText('P2')).toBeTruthy()
    expect(screen.getByText('Observed fact.')).toBeTruthy()
    expect(screen.queryByText(/tokens|Lines|Recovery|document/)).toBeNull()
    expect(view.container.querySelectorAll('[role="meter"]')).toHaveLength(0)
  })

  it('keeps progress truthful while searching and reveals only the requested records', () => {
    const view = render(<RetrievalChunks query="worker" chunks={[scored, unscored]} visibleCount={1} searching />)
    expect(screen.getByText('Retrieving')).toBeTruthy()
    expect(screen.getByText('Operations runbook')).toBeTruthy()
    expect(screen.queryByText('Shift notes')).toBeNull()
    view.rerender(<RetrievalChunks query="worker" chunks={[scored, unscored]} visibleCount={2} searching={false} />)
    expect(screen.queryByText('Retrieving')).toBeNull()
    expect(screen.getByText('2 passages')).toBeTruthy()
    expect(screen.getByText('Shift notes')).toBeTruthy()
    expect(screen.getAllByRole('meter')).toHaveLength(1)
  })

  it('keeps an empty result empty without a fabricated passage, score, or action', () => {
    const view = render(<RetrievalChunks query="unmatched" chunks={[]} visibleCount={10} searching={false} />)
    expect(screen.getByText('0 passages')).toBeTruthy()
    expect(view.container.querySelectorAll('[role="meter"]')).toHaveLength(0)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByText('Operations runbook')).toBeNull()
  })
})

describe('RetrievalChunks stable-ID actions', () => {
  it('makes only callback-backed rows actionable and returns the original record', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn<(chunk: RetrievalChunk) => void>()
    const view = render(<RetrievalChunks query="recovery" chunks={[scored, unscored]} visibleCount={2}
      searching={false} onSelect={onSelect} />)
    const first = screen.getByRole('button', { name: 'Open source Operations runbook, L40–L55' })
    const second = screen.getByRole('button', { name: 'Open source Shift notes, L8' })
    expect(view.container.querySelectorAll('button')).toHaveLength(2)
    await user.click(second)
    expect(onSelect).toHaveBeenCalledWith(unscored)
    expect(onSelect.mock.calls[0][0].id).toBe('knowledge-77')
    await user.click(first)
    expect(onSelect).toHaveBeenCalledWith(scored)
    expect(onSelect.mock.calls[1][0].deepLink).toBe('/knowledge/knowledge-42')
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('opens the same real chunk through keyboard activation', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn<(chunk: RetrievalChunk) => void>()
    render(<RetrievalChunks query="recovery" chunks={[scored]} visibleCount={1} searching={false} onSelect={onSelect} />)
    const row = screen.getByRole('button', { name: 'Open source Operations runbook, L40–L55' })
    row.focus()
    expect(document.activeElement).toBe(row)
    await user.keyboard('{Enter}')
    await user.keyboard(' ')
    expect(onSelect).toHaveBeenCalledTimes(2)
    expect(onSelect.mock.calls.map(([chunk]) => chunk.id)).toEqual(['knowledge-42', 'knowledge-42'])
  })

  it('keeps the original donor score and text visible inside an actionable row', () => {
    const onSelect = vi.fn<(chunk: RetrievalChunk) => void>()
    render(<RetrievalChunks query="recovery" chunks={[scored]} visibleCount={1} searching={false} onSelect={onSelect} />)
    const row = screen.getByRole('button', { name: 'Open source Operations runbook, L40–L55' })
    expect(within(row).getByText('0.91')).toBeTruthy()
    expect(within(row).getByText('Restart the worker after the queue drains.')).toBeTruthy()
    expect(within(row).getByText('82 tokens')).toBeTruthy()
    expect(within(row).getByRole('meter')).toHaveAttribute('aria-valuenow', '91')
    fireEvent.click(row)
    expect(onSelect).toHaveBeenCalledWith(scored)
  })
})
