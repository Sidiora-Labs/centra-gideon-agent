import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WebSearch, type WebSearchResult } from '../../../vendor/assistant-ui/elements/web-search'
import { RetrievalChunks, type RetrievalChunk } from '../../../vendor/assistant-ui/elements/retrieval-chunks'
import { InlineCitation, type Source } from '../../../vendor/assistant-ui/elements/inline-citation'
import { ImageGeneration } from '../../../vendor/assistant-ui/elements/image-generation'
import { DocumentReference, type DocumentAnchor } from '../../../vendor/assistant-ui/elements/document-reference'
import { MemoryChips, type MemoryChip } from '../../../vendor/assistant-ui/elements/memory-chips'
import { ResearchReport, type ReportSection } from '../../../vendor/assistant-ui/elements/research-report'

const webResults: WebSearchResult[] = [
  { title: 'Incident timeline', domain: 'ops.example.org' },
  { title: 'Recovery procedure', domain: 'runbooks.example.org' },
  { title: 'Service status', domain: 'status.example.org' },
]

const chunks: RetrievalChunk[] = [
  { id: 'recovery-1', source: 'Incident timeline', locator: 'L18–24', score: 0.83, text: 'The worker recovered after restart.' },
  { id: 'recovery-2', source: 'Recovery procedure', locator: 'p. 4', score: 0.42, text: 'The queue was drained before replay.' },
]

const sources: Source[] = [
  { domain: 'ops.example.org', title: 'Incident timeline', snippet: 'Observed restart at 08:15.' },
  { domain: 'runbooks.example.org', title: 'Recovery procedure', snippet: 'Drain queue before replay.' },
]

const anchors: DocumentAnchor[] = [
  { page: 2, quote: 'The queue was drained.' },
  { page: 2, quote: 'Replay began at 08:17.' },
  { page: 4, quote: 'No messages were lost.' },
]

const memory: MemoryChip[] = [
  { id: 'preference-1', text: 'No overnight alerts', change: 'existing' },
  { id: 'incident-2', text: 'Queue recovered', change: 'added' },
  { id: 'procedure-3', text: 'Drain before replay', change: 'updated' },
]

const sections: ReportSection[] = [
  { id: 'summary', heading: 'Summary', state: 'done', sources: 2, preview: 'Worker recovered at 08:15.' },
  { id: 'timeline', heading: 'Timeline', state: 'writing', sources: 1 },
  { id: 'follow-up', heading: 'Follow-up', state: 'pending', sources: 0 },
]

describe('web search supplied-result limits', () => {
  it('changes visible rows without changing the producer-reported total', () => {
    const { rerender, container } = render(<WebSearch query="worker recovery" results={webResults}
      visibleResults={1.9} searching={false} cycle={4} />)
    const view = within(container.querySelector('[data-slot="web-search"]') as HTMLElement)
    expect(view.getByText('worker recovery')).toBeInTheDocument()
    expect(view.getByText('3 results')).toBeInTheDocument()
    expect(view.getByText('Incident timeline')).toBeInTheDocument()
    expect(view.queryByText('Recovery procedure')).toBeNull()

    rerender(<WebSearch query="worker recovery" results={webResults} visibleResults={99} searching={false} cycle={4} />)
    expect(view.getByText('Recovery procedure')).toBeInTheDocument()
    expect(view.getByText('Service status')).toBeInTheDocument()
    rerender(<WebSearch query="worker recovery" results={webResults} visibleResults={-1} searching={false} cycle={4} />)
    expect(view.getByText('3 results')).toBeInTheDocument()
    expect(view.queryByText('Incident timeline')).toBeNull()
  })

  it('does not announce completed results while searching, including an empty search', () => {
    const { rerender } = render(<WebSearch query="service status" results={webResults}
      visibleResults={0} searching cycle={1} />)
    expect(screen.getByText('Searching')).toBeInTheDocument()
    expect(screen.queryByText('3 results')).toBeNull()
    rerender(<WebSearch query="service status" results={[]} visibleResults={8} searching={false} cycle={2} />)
    expect(screen.getByText('0 results')).toBeInTheDocument()
    expect(screen.queryByText('Searching')).toBeNull()
    expect(screen.queryByText('Incident timeline')).toBeNull()
  })
})

describe('retrieval passages and announced relevance', () => {
  it('limits passages while retaining the actual result count and individual locators', () => {
    const { rerender } = render(<RetrievalChunks query="worker recovery" chunks={chunks}
      visibleCount={1.8} searching={false} />)
    expect(screen.getByText('2 passages')).toBeInTheDocument()
    expect(screen.getByText('L18–24')).toBeInTheDocument()
    expect(screen.queryByText('p. 4')).toBeNull()
    rerender(<RetrievalChunks query="worker recovery" chunks={chunks} visibleCount={2} searching={false} />)
    expect(screen.getAllByRole('meter')).toHaveLength(2)
    expect(screen.getByText('The queue was drained before replay.')).toBeInTheDocument()
    rerender(<RetrievalChunks query="worker recovery" chunks={chunks} visibleCount={Number.NaN} searching={false} />)
    expect(screen.getByText('2 passages')).toBeInTheDocument()
    expect(screen.queryByRole('meter')).toBeNull()
  })

  it('clamps announced meter values but preserves the recorded score text', () => {
    const outOfRange: RetrievalChunk[] = [
      { ...chunks[0], score: 1.2 }, { ...chunks[1], score: -0.4 },
    ]
    render(<RetrievalChunks query="worker recovery" chunks={outOfRange} visibleCount={2} searching={false} />)
    const high = screen.getByRole('meter', { name: 'Incident timeline relevance score' })
    const low = screen.getByRole('meter', { name: 'Recovery procedure relevance score' })
    expect(high).toHaveAttribute('aria-valuemin', '0')
    expect(high).toHaveAttribute('aria-valuemax', '100')
    expect(high).toHaveAttribute('aria-valuenow', '100')
    expect(high).toHaveAttribute('aria-valuetext', '1.20 of 1.00')
    expect(high.querySelector('[style]')).toHaveStyle({ width: '100%' })
    expect(low).toHaveAttribute('aria-valuenow', '0')
    expect(low).toHaveAttribute('aria-valuetext', '-0.40 of 1.00')
    expect(low.querySelector('[style]')).toHaveStyle({ width: '0%' })
  })

  it('keeps searching and empty states distinct from measured passages', () => {
    const { rerender } = render(<RetrievalChunks query="queue" chunks={chunks} visibleCount={0} searching />)
    expect(screen.getByText('Retrieving')).toBeInTheDocument()
    expect(screen.queryByText('2 passages')).toBeNull()
    expect(screen.queryByRole('meter')).toBeNull()
    rerender(<RetrievalChunks query="queue" chunks={[]} visibleCount={8} searching={false} />)
    expect(screen.getByText('0 passages')).toBeInTheDocument()
    expect(screen.queryByRole('meter')).toBeNull()
  })
})

describe('controlled inline citations', () => {
  it('shows only the selected supplied source and closes when the caller changes selection', async () => {
    const onOpenIndexChange = vi.fn()
    const { rerender } = render(<InlineCitation sources={sources} openIndex={1}
      onOpenIndexChange={onOpenIndexChange}>The queue recovered.</InlineCitation>)
    expect(screen.getByText('The queue recovered.')).toBeInTheDocument()
    expect(screen.getAllByRole('button')).toHaveLength(2)
    expect(await screen.findByText('Drain queue before replay.')).toBeInTheDocument()
    expect(screen.queryByText('Observed restart at 08:15.')).toBeNull()

    rerender(<InlineCitation sources={sources} openIndex={0}
      onOpenIndexChange={onOpenIndexChange}>The queue recovered.</InlineCitation>)
    expect(await screen.findByText('Observed restart at 08:15.')).toBeInTheDocument()
    expect(screen.queryByText('Drain queue before replay.')).toBeNull()
    rerender(<InlineCitation sources={sources} openIndex={null}
      onOpenIndexChange={onOpenIndexChange}>The queue recovered.</InlineCitation>)
    await waitFor(() => expect(screen.queryByText('Observed restart at 08:15.')).toBeNull())
  })

  it('requests a selected citation through the real preview trigger', async () => {
    const onOpenIndexChange = vi.fn()
    render(<InlineCitation sources={sources} openIndex={null}
      onOpenIndexChange={onOpenIndexChange}>The queue recovered.</InlineCitation>)
    await userEvent.setup().hover(screen.getAllByRole('button')[1])
    await waitFor(() => expect(onOpenIndexChange).toHaveBeenCalledWith(1))
  })
})

describe('generated image output boundaries', () => {
  it('accepts recorded HTTP and artifact URLs but never renders unsafe or relative output', () => {
    const { rerender } = render(<ImageGeneration prompt="Queue diagram" generating={false}
      imageUrl="https://assets.example.org/queue.png" width={640} height={480} />)
    expect(screen.getByRole('img', { name: 'Queue diagram' })).toHaveAttribute('src', 'https://assets.example.org/queue.png')
    expect(screen.getByText('640 × 480')).toBeInTheDocument()
    rerender(<ImageGeneration prompt="Queue diagram" generating={false} imageUrl="/api/artifacts/queue/raw?v=2" width={640} />)
    expect(screen.getByRole('img')).toHaveAttribute('src', '/api/artifacts/queue/raw?v=2')
    expect(screen.queryByText('640 × 480')).toBeNull()
    for (const imageUrl of ['javascript:alert(1)', 'data:image/svg+xml,<svg/>', '/unscoped/file.png', '//other.example.org/p.png']) {
      rerender(<ImageGeneration prompt="Queue diagram" generating={false} imageUrl={imageUrl} />)
      expect(screen.queryByRole('img')).toBeNull()
      expect(screen.getByText('Image unavailable')).toBeInTheDocument()
    }
  })

  it('suppresses a stale image and regeneration while work is running, then invokes the supplied action', () => {
    const regenerate = vi.fn()
    const props = { prompt: 'Queue diagram', imageUrl: '/api/artifacts/queue/raw', onRegenerate: regenerate }
    const { rerender } = render(<ImageGeneration {...props} generating />)
    expect(screen.getByText('Generating')).toBeInTheDocument()
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Regenerate image' })).toBeNull()
    rerender(<ImageGeneration {...props} generating={false} />)
    expect(screen.getByRole('img', { name: 'Queue diagram' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate image' }))
    expect(regenerate).toHaveBeenCalledOnce()
  })
})

describe('document anchors and memory changes', () => {
  it('marks only the first matching anchor current and jumps to the exact selected page', () => {
    const onJump = vi.fn()
    const { rerender } = render(<DocumentReference title="Incident review" pages={6}
      anchors={anchors} activePage={2} onJump={onJump} />)
    expect(screen.getByText('6 pages · 3 cited')).toBeInTheDocument()
    const buttons = screen.getAllByRole('button')
    expect(buttons).toHaveLength(3)
    expect(buttons[0]).toHaveAttribute('aria-current', 'true')
    expect(buttons[1]).not.toHaveAttribute('aria-current')
    fireEvent.click(buttons[2])
    expect(onJump).toHaveBeenCalledWith(4)
    rerender(<DocumentReference title="Incident review" pages={6} anchors={anchors} activePage={4} onJump={onJump} />)
    expect(screen.getAllByRole('button')[2]).toHaveAttribute('aria-current', 'true')
    expect(screen.getAllByRole('button')[0]).not.toHaveAttribute('aria-current')
  })

  it('preserves cited text without inventing a jump action for a read-only document', () => {
    render(<DocumentReference title="Incident review" pages={6} anchors={anchors} activePage={2} />)
    expect(screen.getByText('The queue was drained.')).toBeInTheDocument()
    expect(screen.getByText('Replay began at 08:17.')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('counts only new or updated memories and passes the exact selected ID to forget', () => {
    const onForget = vi.fn()
    const { rerender } = render(<MemoryChips chips={memory} onForget={onForget} />)
    expect(screen.getByText('remembered 2')).toBeInTheDocument()
    expect(screen.getAllByRole('button')).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: 'Forget "Drain before replay"' }))
    expect(onForget).toHaveBeenCalledOnce()
    expect(onForget).toHaveBeenCalledWith('procedure-3')
    rerender(<MemoryChips chips={[memory[0]]} onForget={onForget} />)
    expect(screen.getByText('memory')).toBeInTheDocument()
    expect(screen.queryByText('remembered 2')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Forget "Drain before replay"' })).toBeNull()
  })

  it('renders empty memory and document records without fabricated controls', () => {
    render(<><MemoryChips chips={[]} /><DocumentReference title="Blank note" pages={0} anchors={[]} activePage={0} /></>)
    expect(screen.getByText('memory')).toBeInTheDocument()
    expect(screen.getByText('0 pages · 0 cited')).toBeInTheDocument()
    expect(screen.queryByRole('button')).toBeNull()
  })
})

describe('research report supplied progress', () => {
  it('updates completion from section states and keeps only recorded previews and source counts', () => {
    const { rerender } = render(<ResearchReport title="Queue recovery" sections={sections} sourcesRead={3} />)
    expect(screen.getByText('1/3 sections · 3 sources read')).toBeInTheDocument()
    expect(screen.getByText('Worker recovered at 08:15.')).toBeInTheDocument()
    expect(screen.getByText('2 src')).toBeInTheDocument()
    expect(screen.getByText('1 src')).toBeInTheDocument()
    expect(screen.queryByText('0 src')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()

    rerender(<ResearchReport title="Queue recovery" sections={sections.map(section => ({ ...section, state: 'done' as const }))}
      sourcesRead={4} />)
    expect(screen.getByText('3/3 sections · 4 sources read')).toBeInTheDocument()
    expect(screen.getByText('Follow-up')).toBeInTheDocument()
  })

  it('reports an empty report as empty, without implied sources or sections', () => {
    const { container } = render(<ResearchReport title="New investigation" sections={[]} sourcesRead={0} />)
    expect(screen.getByText('0/0 sections · 0 sources read')).toBeInTheDocument()
    expect(container.querySelector('[data-slot="research-report"]')).toBeInTheDocument()
    expect(screen.queryByText('Summary')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })
})
