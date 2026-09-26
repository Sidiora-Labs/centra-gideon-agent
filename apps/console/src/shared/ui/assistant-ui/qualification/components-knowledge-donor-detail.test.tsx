import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { WebSearch } from '../../../vendor/assistant-ui/elements/web-search'
import { RetrievalChunks } from '../../../vendor/assistant-ui/elements/retrieval-chunks'
import { DocumentReference } from '../../../vendor/assistant-ui/elements/document-reference'
import { MapAnswer } from '../../../vendor/assistant-ui/elements/map-answer'

const results = [
  { title: 'Incident review', domain: 'docs.example.org' },
  { title: 'Runbook', domain: 'runbooks.example.org' },
]
const chunks = [
  { id: 'first', source: 'Incident review', locator: 'L18', score: 0.9, text: 'Worker recovered.' },
  { id: 'second', source: 'Runbook', locator: 'L4', score: 0.4, text: 'Restart procedure.' },
]
const anchors = [
  { page: 3, quote: 'First observed recovery.' },
  { page: 3, quote: 'Second observation.' },
  { page: 8, quote: 'Follow-up action.' },
]
const pins = [
  { id: 'berlin', label: 'Berlin', detail: '52.52, 13.405', x: 53.72, y: 20.82 },
  { id: 'paris', label: 'Paris', detail: '48.8566, 2.3522', x: 50.65, y: 22.86 },
]

describe('donor search progression from supplied results', () => {
  it('shows a pending label and only the permitted visible result', () => {
    render(<WebSearch query="recovery" results={results} visibleResults={1} searching cycle={4} />)
    expect(screen.getByText('Searching')).toBeTruthy()
    expect(screen.getByText('Incident review')).toBeTruthy()
    expect(screen.queryByText('Runbook')).toBeNull()
    expect(screen.queryByText('2 results')).toBeNull()
  })

  it('shows a completed count while keeping undisclosed results hidden', () => {
    render(<WebSearch query="recovery" results={results} visibleResults={0} searching={false} cycle={4} />)
    expect(screen.getByText('2 results')).toBeTruthy()
    expect(screen.queryByText('Incident review')).toBeNull()
    expect(screen.queryByText('Runbook')).toBeNull()
  })

  it('reveals the exact source titles and domains supplied by the caller', () => {
    render(<WebSearch query="recovery" results={results} visibleResults={2} searching={false} cycle={4} />)
    expect(screen.getByText('Incident review')).toBeTruthy()
    expect(screen.getByText('Runbook')).toBeTruthy()
    expect(screen.getByText('docs.example.org')).toBeTruthy()
    expect(screen.getByText('runbooks.example.org')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('donor retrieval progression from supplied passages', () => {
  it('withholds a completion count and nonvisible passage during retrieval', () => {
    render(<RetrievalChunks query="recovery" chunks={chunks} visibleCount={1} searching />)
    expect(screen.getByText('Retrieving')).toBeTruthy()
    expect(screen.getByText('Worker recovered.')).toBeTruthy()
    expect(screen.queryByText('Restart procedure.')).toBeNull()
    expect(screen.queryByText('2 passages')).toBeNull()
  })

  it('exposes both recorded scores with correctly bounded meter values', () => {
    render(<RetrievalChunks query="recovery" chunks={chunks} visibleCount={2} searching={false} />)
    expect(screen.getByText('2 passages')).toBeTruthy()
    expect(screen.getByText('0.90')).toBeTruthy()
    expect(screen.getByText('0.40')).toBeTruthy()
    const high = screen.getByRole('meter', { name: 'Incident review relevance score' })
    const low = screen.getByRole('meter', { name: 'Runbook relevance score' })
    expect(high.getAttribute('aria-valuenow')).toBe('90')
    expect(low.getAttribute('aria-valuenow')).toBe('40')
    expect(high.querySelector('span')?.getAttribute('style')).toContain('90%')
    expect(low.querySelector('span')?.getAttribute('style')).toContain('40%')
  })

  it('does not display passages before they become visible', () => {
    render(<RetrievalChunks query="recovery" chunks={chunks} visibleCount={0} searching={false} />)
    expect(screen.getByText('2 passages')).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
  })
})

describe('donor document navigation', () => {
  it('shows the true page count, each quote, and no unused jump action', () => {
    render(<DocumentReference title="Incident review" pages={11} anchors={anchors} activePage={3} />)
    expect(screen.getByText('11 pages · 3 cited')).toBeTruthy()
    expect(screen.getByText('First observed recovery.')).toBeTruthy()
    expect(screen.getByText('Second observation.')).toBeTruthy()
    expect(screen.getByText('Follow-up action.')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
    expect(document.querySelectorAll('[aria-current="true"]')).toHaveLength(1)
  })

  it('passes the selected page to the supplied navigation action', () => {
    function Harness() {
      const [page, setPage] = useState(3)
      return <><DocumentReference title="Incident review" pages={11} anchors={anchors}
        activePage={page} onJump={setPage} /><output aria-label="Selected page">{page}</output></>
    }
    render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: /Follow-up action/ }))
    expect(screen.getByRole('status', { name: 'Selected page' }).textContent).toBe('8')
    expect(document.querySelectorAll('[aria-current="true"]')).toHaveLength(1)
  })
})

describe('donor coordinate grid', () => {
  it('marks supplied coordinates and hides a route unless one is requested', () => {
    const { container } = render(<MapAnswer pins={pins} activeId="berlin" />)
    expect(screen.getByRole('img', { name: 'Berlin' }).getAttribute('style')).toContain('53.72%')
    expect(screen.getByText('52.52, 13.405')).toBeTruthy()
    expect(screen.getByText('48.8566, 2.3522')).toBeTruthy()
    expect(container.querySelector('polyline')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders only the caller requested segment between recorded pins', () => {
    const { container, rerender } = render(<MapAnswer pins={pins} activeId="paris" route />)
    expect(container.querySelector('polyline')?.getAttribute('points')).toBe('53.72,20.82 50.65,22.86')
    rerender(<MapAnswer pins={pins.slice(0, 1)} activeId="berlin" route />)
    expect(container.querySelector('polyline')).toBeNull()
  })

  it('sends the exact selected location ID to the supplied action', () => {
    function Harness() {
      const [selected, select] = useState('berlin')
      return <><MapAnswer pins={pins} activeId={selected} onSelect={select} />
        <output aria-label="Selected location">{selected}</output></>
    }
    render(<Harness />)
    fireEvent.click(screen.getAllByRole('button', { name: 'Paris' })[0])
    expect(screen.getByRole('status', { name: 'Selected location' }).textContent).toBe('paris')
  })
})
