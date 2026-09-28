import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { SessionSearchAnswer } from '../../shared/data/api'
import { ChatSearchCoverage } from './ChatSearchCoverage'

const partial: SessionSearchAnswer = {
  sessions: [], source: 'scan', searched: { chats: 500, of: 12000 },
  complete: false, index: { indexed: 300, of: 12000, building: true, long: 0 }, matched: 0,
}

function SearchState({ answer = partial, pending = false, error = false, query = 'invoice' }: {
  answer?: SessionSearchAnswer | null; pending?: boolean; error?: boolean; query?: string
}) {
  const [all, setAll] = useState(false)
  return <>
    <ChatSearchCoverage query={query} answer={answer} pending={pending} error={error}
      onSearchAll={() => setAll(true)} />
    <div aria-label="Requested coverage">{all ? 'all chats' : 'default coverage'}</div>
  </>
}

afterEach(cleanup)

describe('chat content search coverage', () => {
  it('discloses partial coverage even with no matches and requests all chats explicitly', () => {
    render(<SearchState />)
    expect(screen.getByRole('status')).toHaveTextContent('Searched 500 of 12000 chats. Results may be incomplete.')
    expect(screen.getByRole('status')).toHaveTextContent('Search index is still being built.')
    fireEvent.click(screen.getByRole('button', { name: 'Search all chats' }))
    expect(screen.getByLabelText('Requested coverage')).toHaveTextContent('all chats')
  })

  it('reports complete coverage only from the authoritative answer', () => {
    render(<SearchState answer={{ ...partial, searched: { chats: 12000, of: 12000 }, complete: true, index: null }} />)
    expect(screen.getByRole('status')).toHaveTextContent('Searched all 12000 chats.')
    expect(screen.queryByRole('button', { name: 'Search all chats' })).toBeNull()
  })

  it('does not treat an unavailable index or an unexecuted query as complete', () => {
    render(<SearchState answer={{ ...partial, source: 'none', searched: { chats: 0, of: 12000 }, index: null }} />)
    expect(screen.getByRole('status')).toHaveTextContent('Searched 0 of 12000 chats. Results may be incomplete.')
    expect(screen.getByRole('button', { name: 'Search all chats' })).toBeVisible()
  })

  it('reports failed content reads separately from title matches', () => {
    render(<SearchState answer={null} error />)
    expect(screen.getByRole('status')).toHaveTextContent('Chat contents could not be searched. Title matches are still shown.')
    expect(screen.getByRole('button', { name: 'Search all chats' })).toBeVisible()
  })

  it('announces an active read without offering a duplicate search', () => {
    render(<SearchState pending />)
    expect(screen.getByRole('status')).toHaveTextContent('Searching chat contents…')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('does not claim content coverage for a short query', () => {
    render(<SearchState query="a" />)
    expect(screen.queryByRole('status')).toBeNull()
  })
})
