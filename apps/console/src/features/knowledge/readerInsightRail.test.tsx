import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { KnowledgeDetail } from './KnowledgeDetail'
import { KnowledgeDetailPage } from './KnowledgeDetailPage'
import { ReaderInsights, hasReaderInsights } from './KnowledgeDetailPage'
import { api, type KnowledgeAnnotation, type KnowledgeItem } from '../../shared/data/api'
import * as store from './knowledgeStore'


const LONG_ARTICLE = [
  '# Widgets, considered',
  '',
  'The quick brown fox jumps over the lazy dog, at length, for several lines.',
  '',
  '## How widgets are made',
  '',
  'Another paragraph about the making of widgets.',
].join('\n')

function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 'k1',
    title: 'Widgets, considered',
    content: LONG_ARTICLE,
    item_type: 'note',
    word_count: 440,
    entities: [{ id: 'e1', name: 'Widget Co', entity_type: 'org' }],
    ...over,
  } as KnowledgeItem
}

function annotation(over: Partial<KnowledgeAnnotation> = {}): KnowledgeAnnotation {
  return {
    id: 'a1',
    item_id: 'k1',
    quote: 'quick brown fox',
    occurrence: 0,
    note: '',
    created_at: '2026-08-19T00:00:00',
    ...over,
  }
}

const RELATED = [{ id: 'k2', title: 'A neighbouring note', shared_entities: 3 } as KnowledgeItem]

function stubDetailMount() {
  vi.spyOn(store, 'getKnowledge').mockResolvedValue(null)
  vi.spyOn(api, 'knowledgeItemIntents').mockResolvedValue({ outcomes: [] } as never)
  vi.spyOn(api, 'knowledgeItemGraph').mockRejectedValue(new Error('no graph'))
  vi.spyOn(api, 'knowledgeTags').mockResolvedValue([] as never)
  vi.spyOn(api, 'knowledgeStaleness').mockRejectedValue(new Error('no staleness'))
}

function renderReader(over: Partial<KnowledgeItem> = {}, annotations = [annotation()], related = RELATED) {
  const it_ = item(over)
  return render(
    <KnowledgeDetail
      item={it_}
      reading
      annotations={annotations}
      onChanged={() => {}}
      onDeleted={() => {}}
      insightRail={hasReaderInsights(it_, related, annotations) ? (
        <ReaderInsights item={it_} related={related} annotations={annotations}
          onRemoveAnnotation={() => {}} onOpenItem={() => {}} />
      ) : undefined}
    />,
  )
}

const articleBody = () => screen.getByRole('group', { name: 'Article body' })
const rail = () => screen.getByLabelText('Article outline & insights')

const VIEWPORT_VARIANT = /(?:^|\s)(?:max-)?(?:sm|md|lg|xl|2xl):/
const CONTAINER_VARIANT = /(?:^|\s)@(?:min-\[[^\]]+\]|max-\[[^\]]+\]|[a-z0-9]+):/

function variants(el: HTMLElement) {
  const classes = el.className.split(/\s+/).filter(Boolean)
  return {
    container: classes.filter((c) => CONTAINER_VARIANT.test(c)),
    viewport: classes.filter((c) => VIEWPORT_VARIANT.test(c)),
  }
}

afterEach(() => { vi.restoreAllMocks() })

describe('reading mode no longer replaces the insights dock', () => {
  beforeEach(stubDetailMount)

  it('keeps highlights, entities and related items reachable inside the reader', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    expect(articleBody().textContent, 'the article body is what reading mode is FOR')
      .toContain('The quick brown fox jumps over the lazy dog')

    expect(rail().textContent, 'the user\'s own highlights').toContain('quick brown fox')
    expect(rail().textContent, 'the entities extracted from it').toContain('Widget Co')
    expect(rail().textContent, 'and what it is related to').toContain('A neighbouring note')
  })

  it('renders the dock\'s OWN section components, not a second copy of them', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    expect(rail().textContent).toContain('Highlights · 1')
    expect(rail().textContent).toContain('Entities · 1')
    expect(rail().textContent).toContain('Related · 1')
  })

  it('offers no rail at all when there is nothing to put in it', async () => {
    renderReader({ entities: [], content: 'One paragraph, no headings, nothing extracted.' }, [], [])
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    expect(articleBody().textContent).toContain('One paragraph, no headings')
    expect(hasReaderInsights(item({ entities: [] }), [], [])).toBe(false)
    expect(screen.queryByLabelText(/^Article (outline|insights)/)).toBeNull()
    expect(screen.queryByRole('button', { name: /Insights/ }), 'nor a fold-out for nothing').toBeNull()
  })
})

describe('the rail is split by the READER PANE, not the viewport', () => {
  beforeEach(stubDetailMount)

  it('puts the rail on the same axis as the article, under one split element', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const split = rail().parentElement!
    expect(split.contains(articleBody()), 'article and rail must be siblings of one split')
      .toBe(true)
    expect(rail().tagName, 'a landmark a screen-reader user can jump to').toBe('ASIDE')
  })

  it('decides the split with a container variant resolving against the PANE', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const split = rail().parentElement!

    const v = variants(split)
    expect(v.container, `the split must be governed by a container query: ${split.className}`)
      .toContain('@min-[58rem]:flex-row')
    expect(v.viewport, 'a viewport breakpoint here is the bug the clause names').toEqual([])
    expect(variants(rail()).viewport, 'and the rail itself must not read one either').toEqual([])

    let pane: HTMLElement | null = split.parentElement
    while (pane && !pane.className.split(/\s+/).includes('@container')) pane = pane.parentElement
    expect(pane, 'no @container ancestor — the container variants above resolve against nothing')
      .not.toBeNull()
    expect(pane!.contains(split), 'the pane contains the split').toBe(true)
    expect(pane!.textContent, 'and the whole reader, progress indicator included').toMatch(/% read/)
  })

  it('does not read the window width — the behavioural half of the same claim', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const before = rail().className

    for (const w of [400, 2000]) {
      Object.defineProperty(window, 'innerWidth', { value: w, writable: true, configurable: true })
      window.dispatchEvent(new Event('resize'))
      await new Promise((r) => setTimeout(r, 0))
      expect(rail().className, `the rail changed when only the WINDOW did (${w}px)`).toBe(before)
    }
  })
})

describe('the narrow-pane fold-out', () => {
  beforeEach(stubDetailMount)

  it('announces its expanded state and flips the rail between hidden and shown', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const toggle = screen.getByRole('button', { name: /Insights/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(rail().className.split(/\s+/), 'folded away in a one-column pane').toContain('hidden')

    fireEvent.click(toggle)
    await waitFor(() => expect(toggle).toHaveAttribute('aria-expanded', 'true'))
    expect(rail().className.split(/\s+/)).toContain('flex')
    expect(rail().className.split(/\s+/)).not.toContain('hidden')
  })

  it('is itself container-gated, and the rail stays unconditional above the threshold', async () => {
    renderReader()
    await waitFor(() => expect(articleBody()).toBeInTheDocument())
    const gate = screen.getByRole('button', { name: /Insights/ }).parentElement!
    expect(variants(gate).container).toContain('@min-[58rem]:hidden')
    expect(variants(gate).viewport).toEqual([])

    expect(variants(rail()).container).toContain('@min-[58rem]:flex')
  })
})

describe('the page actually supplies the rail — the last mile', () => {
  beforeEach(() => {
    stubDetailMount()
    vi.spyOn(api, 'knowledgeItem').mockResolvedValue(item() as never)
    vi.spyOn(api, 'knowledgeExtracted').mockResolvedValue({ contents: [] } as never)
    vi.spyOn(api, 'knowledgeItemRelated').mockResolvedValue(RELATED as never)
    vi.spyOn(api, 'knowledgeAnnotations').mockResolvedValue([annotation()] as never)
    vi.spyOn(api, 'knowledgeDuplicates').mockRejectedValue(new Error('no duplicates'))
  })

  it('mounts the rail from the real page, with the real fetched data', async () => {
    render(
      <KnowledgeDetailPage id="k1" onBack={() => {}} onOpenItem={() => {}}
        query={{ read: '1' }} setQuery={() => {}} />,
    )
    await waitFor(() => expect(screen.getByRole('group', { name: 'Article body' })).toBeInTheDocument())
    expect(articleBody().textContent).toContain('The quick brown fox jumps over the lazy dog')
    await waitFor(() => expect(rail().textContent).toContain('A neighbouring note'))
    expect(rail().textContent).toContain('quick brown fox')
  })
})
