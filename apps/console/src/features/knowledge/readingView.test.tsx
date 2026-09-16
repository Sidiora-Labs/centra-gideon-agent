import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ReadingView } from './ReadingView'
import { MARK_CLASS } from './readingAnchors'
import { api, type KnowledgeAnnotation, type KnowledgeItem } from '../../shared/data/api'


const SRC = join(process.cwd(), "src")
const TOKENS = readFileSync(join(SRC, 'shared/theme/tokens.css'), 'utf8')

const LONG_ARTICLE = [
  '# On long articles',
  '',
  'The quick brown fox jumps over the lazy dog. This paragraph exists so a selection has',
  'somewhere to land and so the prose has more than one line of it.',
  '',
  '## A second section',
  '',
  'Another paragraph, with a repeated phrase. Another paragraph, with a repeated phrase.',
].join('\n')

function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return {
    id: 'k1',
    title: 'On long articles',
    content: LONG_ARTICLE,
    item_type: 'note',
    word_count: 440,
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
    created_at: '2026-08-16T00:00:00',
    ...over,
  }
}

function stubScroll(el: HTMLElement, scrollTop: number, scrollHeight = 2000, clientHeight = 500) {
  Object.defineProperty(el, 'scrollTop', { value: scrollTop, writable: true, configurable: true })
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
}

function articleRegion(): HTMLElement {
  return screen.getByRole('group', { name: 'Article body' })
}

async function selectByKeyboard(text: string) {
  const region = articleRegion()
  const node = Array.from(region.querySelectorAll('p, li, h1, h2'))
    .flatMap((el) => Array.from(el.childNodes))
    .filter((n): n is Text => n.nodeType === Node.TEXT_NODE)
    .find((n) => n.data.includes(text))
  if (!node) throw new Error(`fixture does not contain ${JSON.stringify(text)} in one text node`)
  const start = node.data.indexOf(text)
  const range = document.createRange()
  range.setStart(node, start)
  range.setEnd(node, start + text.length)
  const selection = window.getSelection()!
  selection.removeAllRanges()
  selection.addRange(range)
  await act(async () => {
    document.dispatchEvent(new Event('selectionchange'))
    await new Promise((r) => setTimeout(r, 20))
  })
}

let matchMediaReduce = false

beforeEach(() => {
  matchMediaReduce = false
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = () =>
      ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) }) as DOMRect
  }
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: q.includes('prefers-reduced-motion') ? matchMediaReduce : false,
    media: q,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }))
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  window.getSelection()?.removeAllRanges()
})


describe('the editorial reading type scale', () => {
  it('hosts the prose in the `reading` scope', () => {
    const { container } = render(
      <ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />,
    )
    const scope = container.querySelector('.reading')
    expect(scope, 'the article body must carry the reading scale scope').toBeTruthy()
    expect(scope!.textContent).toContain('quick brown fox')
  })

  it('the scope is a real rule that resizes prose — not an inert class name', () => {
    expect(TOKENS).toMatch(/\.doc, \.reading \{[^}]*font-size: 1rem;[^}]*line-height: 1\.7/)
    expect(TOKENS).toMatch(/\.doc :is\(p, li\), \.reading :is\(p, li\) \{[^}]*font-size: 1rem/)
    expect(TOKENS).toMatch(/\.doc h1, \.reading h1 \{[^}]*font-size: 1\.9rem/)
  })

  it('the scale block is UNLAYERED, which is what lets it beat the chat-density utilities', () => {
    const block = TOKENS.slice(TOKENS.indexOf('.doc, .reading {'))
    const openLayerBefore = TOKENS.slice(0, TOKENS.indexOf('.doc, .reading {')).lastIndexOf('@layer base {')
    const closeAfterThatLayer = TOKENS.indexOf('\n}', openLayerBefore)
    expect(closeAfterThatLayer).toBeLessThan(TOKENS.indexOf('.doc, .reading {'))
    expect(block.startsWith('.doc, .reading {')).toBe(true)
  })

  it('the article title is not a second h1 for the route', () => {
    const { container } = render(
      <ReadingView item={item({ content: 'Body with no heading at all.' })}
        annotations={[]} onAnnotationsChanged={() => {}} />,
    )
    expect(Array.from(container.querySelectorAll('h1')).filter((h) => !h.closest('.reading'))).toHaveLength(0)
    expect(container.querySelector('h2')?.textContent).toBe('On long articles')
  })

  it('the title is NOT printed twice when the body already opens with it', () => {
    expect(LONG_ARTICLE.split('\n')[0]).toBe('# On long articles')

    const { container } = render(
      <ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />,
    )

    const titleish = Array.from(container.querySelectorAll('h1, h2'))
      .filter((h) => h.textContent === 'On long articles')
    expect(titleish, 'the headline must appear once, from the body').toHaveLength(1)
    expect(titleish[0].closest('.reading'), 'and it is the BODY heading that survives').toBeTruthy()
  })

  it('a body whose first heading differs keeps the reader’s own title', () => {
    const { container } = render(
      <ReadingView item={item({ content: '# A different headline\n\nprose' })}
        annotations={[]} onAnnotationsChanged={() => {}} />,
    )
    expect(container.querySelector('h2')?.textContent).toBe('On long articles')
    expect(container.querySelector('.reading h1')?.textContent).toBe('A different headline')
  })
})


describe('the reading progress indicator', () => {
  it('is named and carries the fraction', () => {
    render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)
    const bar = screen.getByRole('progressbar', { name: /Reading progress/ })
    expect(bar).toHaveAttribute('aria-valuenow')
  })

  it('advances as the article scrolls', async () => {
    render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)
    const region = articleRegion()
    stubScroll(region, 0)
    await act(async () => {
      region.dispatchEvent(new Event('scroll'))
      await new Promise((r) => setTimeout(r, 30))
    })
    expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toHaveAttribute('aria-valuenow', '0')

    stubScroll(region, 750)
    await act(async () => {
      region.dispatchEvent(new Event('scroll'))
      await new Promise((r) => setTimeout(r, 30))
    })
    expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toHaveAttribute('aria-valuenow', '50')
  })

  it('reports the reading length so a reader can decide to start', () => {
    render(<ReadingView item={item({ word_count: 440 })} annotations={[]} onAnnotationsChanged={() => {}} />)
    expect(screen.getByText(/2 min/)).toBeTruthy()
  })

  it('the arc TWEENS toward the new value when motion is allowed', async () => {
    render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)
    const region = articleRegion()
    stubScroll(region, 0)
    await act(async () => {
      region.dispatchEvent(new Event('scroll'))
      await new Promise((r) => setTimeout(r, 30))
    })
    const arc = () => document.querySelectorAll('circle')[1] as SVGCircleElement
    const seen = new Set<string>()
    stubScroll(region, 1500)
    await act(async () => { region.dispatchEvent(new Event('scroll')) })
    for (let i = 0; i < 25; i += 1) {
      await act(async () => { await new Promise((r) => setTimeout(r, 16)) })
      const v = arc().getAttribute('stroke-dashoffset')
      if (v != null) seen.add(Number(v).toFixed(2))
    }
    expect(seen.size, `expected intermediate offsets, saw: ${[...seen].join(', ')}`).toBeGreaterThan(2)
  })
})


describe('the in-reader highlight', () => {
  it('is reachable without a pointer, and says why it is unavailable first', async () => {
    render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)
    const button = screen.getByRole('button', { name: /Highlight selection/ })
    expect(button).toHaveAttribute('aria-disabled', 'true')
    expect(button.getAttribute('title')).toMatch(/Select a passage/)

    await selectByKeyboard('quick brown fox')

    expect(screen.getByRole('button', { name: /Highlight selection/ })).not.toHaveAttribute('aria-disabled', 'true')
  })

  it('persists the selection through the API, with its quote and occurrence', async () => {
    const create = vi.spyOn(api, 'createKnowledgeAnnotation').mockResolvedValue({
      ok: true, annotation: annotation(),
    })
    const changed = vi.fn()
    render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={changed} />)

    await selectByKeyboard('quick brown fox')
    fireEvent.click(screen.getByRole('button', { name: /Highlight selection/ }))

    const note = screen.getByLabelText(/Note on this passage/)
    fireEvent.change(note, { target: { value: 'the reason it matters' } })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Keep highlight' }))
    })

    expect(create).toHaveBeenCalledWith('k1', {
      quote: 'quick brown fox',
      occurrence: 0,
      note: 'the reason it matters',
    })
    expect(changed, 'the owner must re-read so the highlight reappears').toHaveBeenCalled()
  })

  it('a note is optional — a bare highlight still persists', async () => {
    const create = vi.spyOn(api, 'createKnowledgeAnnotation').mockResolvedValue({
      ok: true, annotation: annotation(),
    })
    render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)

    await selectByKeyboard('lazy dog')
    fireEvent.click(screen.getByRole('button', { name: /Highlight selection/ }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Keep highlight' }))
    })

    expect(create).toHaveBeenCalledWith('k1', { quote: 'lazy dog', occurrence: 0, note: undefined })
  })

  it('a highlight the store already holds is painted back into the prose', () => {
    const { container } = render(
      <ReadingView item={item()} annotations={[annotation({ note: 'kept' })]} onAnnotationsChanged={() => {}} />,
    )
    const painted = Array.from(container.querySelectorAll(`.${MARK_CLASS}`))
    expect(painted.map((m) => m.textContent).join('')).toBe('quick brown fox')
    expect(screen.getByText(/1 highlight/)).toBeTruthy()
  })

  it('a highlight whose passage is gone is reported, not silently dropped', () => {
    render(
      <ReadingView item={item()} annotations={[annotation({ quote: 'a passage since deleted' })]}
        onAnnotationsChanged={() => {}} />,
    )
    expect(screen.getByText(/no longer match the text/)).toBeTruthy()
  })

  it('a failed save says so and keeps the composer open', async () => {
    vi.spyOn(api, 'createKnowledgeAnnotation').mockRejectedValue(new Error('nope'))
    render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)

    await selectByKeyboard('quick brown fox')
    fireEvent.click(screen.getByRole('button', { name: /Highlight selection/ }))
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Keep highlight' }))
    })

    expect(screen.getByText(/Could not save that highlight/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Keep highlight' })).toBeTruthy()
  })

  it('Escape abandons the composer', async () => {
    render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)

    await selectByKeyboard('quick brown fox')
    fireEvent.click(screen.getByRole('button', { name: /Highlight selection/ }))
    expect(screen.getByLabelText(/Note on this passage/)).toBeTruthy()

    fireEvent.keyDown(screen.getByLabelText(/Note on this passage/), { key: 'Escape' })

    expect(screen.queryByLabelText(/Note on this passage/)).toBeNull()
  })
})


describe('the reading view is wired into the item page', () => {
  const page = readFileSync(join(SRC, 'features/knowledge/KnowledgeDetailPage.tsx'), 'utf8')
  const detail = readFileSync(join(SRC, 'features/knowledge/KnowledgeDetail.tsx'), 'utf8')

  it('the page owns reading mode as a navigable URL state', () => {
    expect(page).toMatch(/useQueryParam\(query, setQuery, 'read', ''\)/)
    expect(page).toMatch(/reading=\{reading\}/)
    expect(page).toMatch(/onToggleReading=\{toggleReading\}/)
  })

  it('the page owns the highlights and feeds BOTH surfaces from one fetch', () => {
    expect(page).toMatch(/api\.knowledgeAnnotations\(id\)/)
    expect(page).toMatch(/annotations=\{annotations\}/)
    expect(page).toMatch(/<AnnotationList annotations=\{annotations\}/)
  })

  it('the detail renders the reader, gated on there being a body', () => {
    expect(detail).toMatch(/<ReadingView item=\{full\}/)
    expect(detail).toMatch(/const readingMode = reading && readable/)
    expect(detail).toMatch(/label="Reading mode" active=\{reading\}/)
  })

  it('the published header cluster re-renders when reading mode flips', () => {
    const deps = detail.match(/\}, \[full, draft, reingest[^\]]*\]\)/)?.[0] ?? ''
    expect(deps, 'reading must be a dep of the onHeader publish effect').toContain('reading')
  })
})
