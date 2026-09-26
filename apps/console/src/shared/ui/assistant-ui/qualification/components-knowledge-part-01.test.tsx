import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import type { KnowledgeContextCard, KnowledgeContextResult, KnowledgeItem } from '../../../data/api'
import {
  KnowledgeCitation, KnowledgeImage, KnowledgeImageGallery, KnowledgeLinkPreview,
  KnowledgeRetrievalChunks, KnowledgeWebSearch,
} from '../../../../features/chat/auiKnowledgeResults'
import { AuiKnowledgePanel, splitKnowledgeResults } from '../../../../features/knowledge/auiKnowledgePanel'
import { WebSearch } from '../../../vendor/assistant-ui/elements/web-search'
import { InlineCitation } from '../../../vendor/assistant-ui/elements/inline-citation'
import { ImageGeneration } from '../../../vendor/assistant-ui/elements/image-generation'
import { RetrievalChunks } from '../../../vendor/assistant-ui/elements/retrieval-chunks'

const source: KnowledgeContextCard = {
  id: 'item-real-id',
  title: 'Incident review',
  provider: 'native',
  tokens: 42,
  content: 'The worker recovered after the restart.',
  summary: 'Recovery was observed.',
  source_type: 'document',
  section: 'Timeline',
  line_range: [18, 24],
  deep_link: 'https://docs.example.org/incident',
}

const search: KnowledgeContextResult = {
  query: 'worker recovery',
  results: [source],
  total_tokens: 42,
  max_tokens: 4000,
}

const image: KnowledgeItem = {
  id: 'image-actual-id',
  title: 'Recorded diagram',
  type: 'image',
  mime_type: 'image/png',
  url: 'https://assets.example.org/diagram.png',
}

function OpenHarness({ child }: { child: (onOpen: (id: string) => void) => React.ReactNode }) {
  const [opened, setOpened] = useState<string | null>(null)
  return <>{child(setOpened)}<output aria-label="Opened source">{opened}</output></>
}

describe('knowledge result adapters', () => {
  it('names the query and count supplied by the retrieval API', () => {
    render(<KnowledgeWebSearch result={search} />)
    expect(screen.getByRole('region', { name: 'Knowledge search results for worker recovery' })).toBeTruthy()
    expect(screen.getByText('worker recovery')).toBeTruthy()
    expect(screen.getByText('1 results')).toBeTruthy()
    expect(screen.getByText('Recovery was observed.')).toBeTruthy()
    expect(screen.queryByText('Read 3 sources')).toBeNull()
  })

  it('preserves the source ID through the citation action', () => {
    render(<OpenHarness child={(onOpen) => <KnowledgeCitation card={source} onOpen={onOpen} />} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open source Incident review, Timeline' }))
    expect(screen.getByRole('status', { name: 'Opened source' }).textContent).toBe('item-real-id')
  })

  it('shows a citation without an action when no opener was supplied', () => {
    render(<KnowledgeCitation card={source} />)
    expect(screen.getByText('Incident review, Timeline')).toBeTruthy()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('renders the recorded passage and locator without inventing a score', () => {
    render(<KnowledgeRetrievalChunks result={search} />)
    expect(screen.getByText('The worker recovered after the restart.')).toBeTruthy()
    expect(screen.getByText('Lines 18–24')).toBeTruthy()
    expect(screen.getByText('42 tokens')).toBeTruthy()
    expect(screen.queryByRole('meter')).toBeNull()
  })

  it('uses the recorded summary if the passage is unavailable', () => {
    render(<KnowledgeRetrievalChunks result={{ ...search, results: [{ ...source, content: undefined }] }} />)
    expect(screen.getByText('Recovery was observed.')).toBeTruthy()
  })

  it('says when neither passage nor summary was returned', () => {
    render(<KnowledgeRetrievalChunks result={{ ...search, results: [{ ...source, content: undefined, summary: undefined }] }} />)
    expect(screen.getByText('No passage text available')).toBeTruthy()
  })

  it('opens a source link with its real host and safe browser boundary', () => {
    render(<KnowledgeLinkPreview title={source.title} url={source.deep_link} description={source.summary} />)
    const link = screen.getByRole('link', { name: 'Incident review' })
    expect(link.getAttribute('href')).toBe('https://docs.example.org/incident')
    expect(link.getAttribute('rel')).toBe('noopener noreferrer')
    expect(screen.getByText('docs.example.org')).toBeTruthy()
  })

  it('never turns an untrusted scheme into a clickable action', () => {
    render(<KnowledgeLinkPreview title="Unsafe bookmark" url="javascript:alert(1)" />)
    expect(screen.getByText('Unsafe bookmark')).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('keeps a relative or absent URL as text', () => {
    render(<KnowledgeLinkPreview title="Local note" url="/knowledge/item-real-id" />)
    expect(screen.getByText('Local note')).toBeTruthy()
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('renders only an image record with an actual image URL', () => {
    render(<KnowledgeImage item={image} />)
    const node = screen.getByRole('img', { name: 'Recorded diagram' })
    expect(node.getAttribute('src')).toBe('https://assets.example.org/diagram.png')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('uses the persisted knowledge file endpoint for an uploaded image', () => {
    render(<KnowledgeImage item={{ ...image, id: 'local/image 1', file_path: 'images/diagram.png', url: undefined }} />)
    expect(screen.getByRole('img', { name: 'Recorded diagram' }).getAttribute('src'))
      .toBe('/api/knowledge/items/local%2Fimage%201/file')
  })

  it('omits records that are not images and images without a usable URL', () => {
    const { container, rerender } = render(<KnowledgeImage item={{ ...image, type: 'document', mime_type: 'text/plain' }} />)
    expect(container.querySelector('figure')).toBeNull()
    rerender(<KnowledgeImage item={{ ...image, url: undefined }} />)
    expect(container.querySelector('figure')).toBeNull()
  })

  it('opens the exact selected image ID', () => {
    render(<OpenHarness child={(onOpen) => <KnowledgeImage item={image} onOpen={onOpen} />} />)
    fireEvent.click(screen.getByRole('button', { name: 'Recorded diagram' }))
    expect(screen.getByRole('status', { name: 'Opened source' }).textContent).toBe('image-actual-id')
  })

  it('filters mixed records into a gallery of actual image items', () => {
    render(<KnowledgeImageGallery items={[image, { ...image, id: 'note', type: 'document', mime_type: 'text/plain' }, { ...image, id: 'bad', url: 'data:text/html,x' }]} />)
    expect(screen.getByRole('region', { name: 'Knowledge images' }).querySelectorAll('img')).toHaveLength(1)
  })

  it('does not show an empty gallery as if images exist', () => {
    const { container } = render(<KnowledgeImageGallery items={[]} />)
    expect(container.querySelector('[data-slot="image-gallery"]')).toBeNull()
  })

  it('starts without claiming a query returned results', () => {
    render(<AuiKnowledgePanel query="" />)
    expect(screen.getByRole('region', { name: 'Knowledge search' })).toBeTruthy()
    expect(screen.queryByText(/results for/)).toBeNull()
  })

  it('keeps saved web sources and other retrieval records in separate views', () => {
    const web = { ...source, id: 'bookmark-1', source_type: 'bookmark' }
    const anotherWeb = { ...source, id: 'web-url-1', source_type: 'web_url' }
    const classified = splitKnowledgeResults({ ...search, results: [source, web, anotherWeb] })
    expect(classified.web.map((card) => card.id)).toEqual(['bookmark-1', 'web-url-1'])
    expect(classified.other.map((card) => card.id)).toEqual(['item-real-id'])
  })
})

describe('adopted donor knowledge elements', () => {
  it('reports the supplied search result count instead of a fixed demo claim', () => {
    render(<WebSearch query="migration" results={[{ title: 'Runbook', domain: 'docs.example.org' }]}
      visibleResults={1} searching={false} cycle={1} />)
    expect(screen.getByText('1 results')).toBeTruthy()
    expect(screen.getByText('Runbook')).toBeTruthy()
    expect(screen.queryByText('Read 3 sources')).toBeNull()
  })

  it('does not claim a completed search while searching', () => {
    render(<WebSearch query="migration" results={[]} visibleResults={0} searching cycle={1} />)
    expect(screen.getByText('Searching')).toBeTruthy()
    expect(screen.queryByText('0 results')).toBeNull()
  })

  it('renders caller supplied citation text and source metadata only', () => {
    function CitationHarness() {
      const [open, setOpen] = useState<number | null>(null)
      return <InlineCitation sources={[{ title: 'Runbook', domain: 'docs.example.org', snippet: 'Observed recovery' }]}
        openIndex={open} onOpenIndexChange={setOpen}>Recovery completed.</InlineCitation>
    }
    render(<CitationHarness />)
    expect(screen.getByText('Recovery completed.')).toBeTruthy()
    expect(screen.queryByText(/Optimistic updates/)).toBeNull()
  })

  it('renders an actual image URL without artwork or a guessed size', () => {
    render(<ImageGeneration prompt="Recorded diagram" generating={false}
      imageUrl="/api/artifacts/diagram/raw?version=2" />)
    expect(screen.getByRole('img', { name: 'Recorded diagram' }).getAttribute('src'))
      .toBe('/api/artifacts/diagram/raw?version=2')
    expect(screen.queryByText('1024 × 1024')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Regenerate image' })).toBeNull()
  })

  it('keeps unknown image output unavailable rather than painting a result', () => {
    render(<ImageGeneration prompt="Recorded diagram" generating={false} />)
    expect(screen.getByText('Image unavailable')).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('offers regeneration only when a caller supplies an action', () => {
    function ImageHarness() {
      const [runs, setRuns] = useState(0)
      return <><ImageGeneration prompt="Recorded diagram" generating={false} imageUrl="/api/artifacts/diagram/raw"
        width={640} height={480} onRegenerate={() => setRuns((n) => n + 1)} /><output>{runs}</output></>
    }
    render(<ImageHarness />)
    expect(screen.getByText('640 × 480')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate image' }))
    expect(screen.getByRole('status').textContent).toBe('1')
  })

  it('uses a busy state without claiming an output image', () => {
    render(<ImageGeneration prompt="Recorded diagram" generating imageUrl="/api/artifacts/diagram/raw" />)
    expect(screen.getByText('Generating')).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('does not claim a retrieval threshold that the producer did not supply', () => {
    render(<RetrievalChunks query="migration" chunks={[{ id: 'chunk-1', source: 'Runbook', locator: 'L10', score: 0.9,
      text: 'The worker recovered.' }]} visibleCount={1} searching={false} />)
    expect(screen.getByText('1 passages')).toBeTruthy()
    expect(screen.getByText('The worker recovered.')).toBeTruthy()
    expect(screen.queryByText(/above threshold/)).toBeNull()
  })
})
