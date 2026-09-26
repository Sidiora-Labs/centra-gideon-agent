import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { KnowledgeItem } from '../../shared/data/api'
import { KnowledgeExtras } from './KnowledgeDetailPage'

const unexpectedAction = () => { throw new Error('Unexpected knowledge action') }

describe('knowledge detail source previews', () => {
  it('opens an actual related image and exposes recorded link and audio sources', () => {
    const item: KnowledgeItem = {
      id: 'saved-page', type: 'bookmark', title: 'Research note',
      url: 'https://example.org/research', summary: 'Original source',
    }
    const related: KnowledgeItem[] = [
      { id: 'chart', type: 'image', title: 'Result chart', url: 'https://example.org/chart.png' },
      { id: 'interview', type: 'audio', title: 'Research interview', url: 'https://example.org/interview.mp3' },
    ]
    let opened = ''
    const { container } = render(<KnowledgeExtras item={item} pool={[]} related={related}
      onOpenItem={(id) => { opened = id }} annotations={[]} onRemoveAnnotation={unexpectedAction}
      duplicates={[]} duplicatesError={null} onRetryDuplicates={unexpectedAction} onMerged={unexpectedAction} />)

    expect(screen.getByRole('link', { name: 'Research note' })).toHaveAttribute('href', item.url)
    expect(screen.getByText('Original source')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Result chart' })).toHaveAttribute('src', related[0]!.url)
    const gallery = container.querySelector('[data-slot="image-gallery"]') as HTMLElement
    expect(gallery).toBeInTheDocument()
    fireEvent.click(within(gallery).getByRole('button', { name: 'Result chart' }))
    expect(opened).toBe('chart')
    expect(container.querySelector('audio[controls]')).toHaveAttribute('src', related[1]!.url)
  })

  it('keeps the truthful empty state when there is no previewable source', () => {
    const item: KnowledgeItem = { id: 'empty', type: 'note', title: 'Empty note' }
    render(<KnowledgeExtras item={item} pool={[]} related={[]} onOpenItem={unexpectedAction}
      annotations={[]} onRemoveAnnotation={unexpectedAction} duplicates={[]} duplicatesError={null}
      onRetryDuplicates={unexpectedAction} onMerged={unexpectedAction} />)
    expect(screen.getByText(/No extracted content, entities, or related items yet/)).toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('uses recorded source titles and falls back to the exact URL when none exists', () => {
    const base: KnowledgeItem = {
      id: 'linked', type: 'bookmark', title: 'Saved bookmark',
      url: 'https://example.org/source', url_title: 'Original page title',
    }
    const props = { pool: [], related: [], onOpenItem: unexpectedAction, annotations: [],
      onRemoveAnnotation: unexpectedAction, duplicates: [], duplicatesError: null,
      onRetryDuplicates: unexpectedAction, onMerged: unexpectedAction }
    const view = render(<KnowledgeExtras item={base} {...props} />)
    expect(screen.getByRole('link', { name: 'Original page title' })).toHaveAttribute('href', base.url)

    view.rerender(<KnowledgeExtras item={{ ...base, url_title: '', title: '' }} {...props} />)
    expect(screen.getByRole('link', { name: base.url })).toHaveAttribute('href', base.url)
  })
})
