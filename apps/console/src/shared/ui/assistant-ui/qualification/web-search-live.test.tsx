import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WebSearch, type WebSearchResult } from '../../../vendor/assistant-ui/elements/web-search'

const records: WebSearchResult[] = [
  {
    id: 'source-incident-9', title: 'Incident timeline', domain: 'docs.example.org',
    summary: 'Worker restart and queue recovery.', url: 'https://docs.example.org/incidents/9',
  },
  {
    id: 'source-runbook-4', title: 'Recovery runbook', domain: 'docs.example.org',
    summary: 'Drain the queue before replay.', url: 'https://docs.example.org/runbooks/4',
  },
]

const base = { query: 'queue recovery', results: records, visibleResults: 2, searching: false, cycle: 1 }

describe('live WebSearch result actions', () => {
  it('keeps a supplied result read-only without a selection callback, while exposing its real external URL', () => {
    const { container } = render(<WebSearch {...base} visibleResults={1} />)
    const view = within(container.querySelector('[data-slot="web-search"]') as HTMLElement)
    expect(view.getByText('queue recovery')).toBeInTheDocument()
    expect(view.getByText('2 results')).toBeInTheDocument()
    expect(view.getByText('Worker restart and queue recovery.')).toBeInTheDocument()
    expect(view.queryByText('Recovery runbook')).toBeNull()
    expect(view.queryByRole('button')).toBeNull()
    const link = view.getByRole('link', { name: 'Open Incident timeline in new tab' })
    expect(link).toHaveAttribute('href', records[0].url)
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('selects the exact stable record by pointer and keyboard, including duplicate domains', async () => {
    const onSelect = vi.fn()
    render(<WebSearch {...base} onSelect={onSelect} />)
    const incident = screen.getByRole('button', { name: 'Select Incident timeline' })
    const runbook = screen.getByRole('button', { name: 'Select Recovery runbook' })
    fireEvent.click(runbook)
    expect(onSelect).toHaveBeenCalledWith(records[1])
    expect(onSelect.mock.calls[0][0].id).toBe('source-runbook-4')
    incident.focus()
    await userEvent.setup().keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledTimes(2)
    expect(onSelect.mock.calls[1][0].id).toBe('source-incident-9')
  })

  it('keeps external opening separate from row selection and reports activation only when observed', () => {
    const onSelect = vi.fn()
    const onOpen = vi.fn()
    render(<WebSearch {...base} onSelect={onSelect} onOpen={onOpen} />)
    const link = screen.getByRole('link', { name: 'Open Recovery runbook in new tab' })
    fireEvent.click(link)
    expect(onOpen).toHaveBeenCalledOnce()
    expect(onOpen).toHaveBeenCalledWith(records[1])
    expect(onSelect).not.toHaveBeenCalled()
    expect(link).toHaveAttribute('href', 'https://docs.example.org/runbooks/4')
  })

  it('offers a safe external link without inventing a selectable result', () => {
    const onOpen = vi.fn()
    render(<WebSearch {...base} visibleResults={1} onOpen={onOpen} />)
    expect(screen.queryByRole('button')).toBeNull()
    fireEvent.click(screen.getByRole('link', { name: 'Open Incident timeline in new tab' }))
    expect(onOpen).toHaveBeenCalledWith(records[0])
  })

  it('omits external links for unsafe, relative, protocol-relative, and malformed URLs', () => {
    const onOpen = vi.fn()
    const { rerender } = render(<WebSearch {...base} results={[{ ...records[0], url: 'javascript:alert(1)' }]}
      visibleResults={1} onOpen={onOpen} />)
    for (const url of ['javascript:alert(1)', 'data:text/html,unsafe', '/knowledge/source-incident-9',
      '//other.example.org/path', 'https://']) {
      rerender(<WebSearch {...base} results={[{ ...records[0], url }]} visibleResults={1} onOpen={onOpen} />)
      expect(screen.getByText('Incident timeline')).toBeInTheDocument()
      expect(screen.queryByRole('link')).toBeNull()
      expect(screen.queryByRole('button')).toBeNull()
    }
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('preserves visible-result limits and cycle remounts without losing stable IDs', () => {
    const onSelect = vi.fn()
    const { rerender } = render(<WebSearch {...base} visibleResults={1} onSelect={onSelect} />)
    const first = screen.getByRole('button', { name: 'Select Incident timeline' })
    expect(screen.queryByRole('button', { name: 'Select Recovery runbook' })).toBeNull()
    rerender(<WebSearch {...base} visibleResults={2} onSelect={onSelect} />)
    fireEvent.click(screen.getByRole('button', { name: 'Select Recovery runbook' }))
    expect(onSelect).toHaveBeenCalledWith(records[1])
    rerender(<WebSearch {...base} visibleResults={2} cycle={2} onSelect={onSelect} />)
    expect(screen.getByRole('button', { name: 'Select Incident timeline' })).not.toBe(first)
    expect(screen.getByText('2 results')).toBeInTheDocument()
    rerender(<WebSearch {...base} visibleResults={0} cycle={2} onSelect={onSelect} />)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('2 results')).toBeInTheDocument()
  })

  it('does not create phantom result actions during an empty search', () => {
    const onSelect = vi.fn()
    const onOpen = vi.fn()
    const { rerender } = render(<WebSearch {...base} results={[]} visibleResults={2}
      searching onSelect={onSelect} onOpen={onOpen} />)
    expect(screen.getByText('Searching')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByRole('link')).toBeNull()
    rerender(<WebSearch {...base} results={[]} visibleResults={2} searching={false}
      onSelect={onSelect} onOpen={onOpen} />)
    expect(screen.getByText('0 results')).toBeInTheDocument()
    expect(onSelect).not.toHaveBeenCalled()
    expect(onOpen).not.toHaveBeenCalled()
  })
})
