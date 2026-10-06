import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { MemorySearchHealth } from './MemoryPanel'

afterEach(cleanup)

describe('Memory search health', () => {
  it('states keyword and semantic index failures and exposes the repair destination', () => {
    render(<MemorySearchHealth health={{ authority: 'journal', state: 'degraded', detail: 'The database is read-only.', repair_id: 'memory.rebuild-fts', semantic_state: 'degraded', semantic_detail: 'Semantic search indexes 0 of 40 embedded memories.' }} />)
    expect(screen.getByText('The database is read-only.')).toBeTruthy()
    expect(screen.getByText('Semantic search indexes 0 of 40 embedded memories.')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open memory diagnostics and repairs' }).getAttribute('href')).toBe('#/settings/doctor')
  })
  it('keeps unavailable native health distinct from a healthy legacy projection', () => {
    render(<MemorySearchHealth health={{ authority: 'hypermid', state: 'unavailable', detail: 'Native memory diagnostics could not be read. Search health is unknown.' }} />)
    expect(screen.getByText('Native memory diagnostics could not be read. Search health is unknown.')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Open native memory diagnostics' }).getAttribute('href')).toBe('#/hypermid')
    expect(screen.queryByText(/semantic search indexes/i)).toBeNull()
  })
  it('reports absent observations as unknown', () => {
    render(<MemorySearchHealth />)
    expect(screen.getByText('Search index health could not be read.')).toBeTruthy()
  })
})
