import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ReadingView } from './ReadingView'
import { parseOutline } from './readingOutline'
import type { KnowledgeItem } from '../../shared/data/api'


const SECTIONED = [
  '# Widgets, considered',
  '',
  'Opening paragraph about widgets, with enough words to be a paragraph.',
  '',
  '## How widgets are made',
  '',
  'The middle section.',
  '',
  '## How widgets are sold',
  '',
  'The last section.',
].join('\n')

const RAW_HTML_HEADING = SECTIONED.replace(
  '## How widgets are sold',
  '<h2>How widgets are sold</h2>',
)

function item(content: string): KnowledgeItem {
  return {
    id: 'k1',
    title: 'Widgets, considered',
    content,
    item_type: 'note',
    word_count: 400,
  } as KnowledgeItem
}

function renderReader(content: string) {
  return render(
    <ReadingView item={item(content)} annotations={[]} onAnnotationsChanged={() => {}} />,
  )
}

const scroller = () => screen.getByRole('group', { name: 'Article body' })
const headings = () => Array.from(scroller().querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6'))
const outlineRows = () => screen.getAllByRole('button', { pressed: false }).concat(screen.queryAllByRole('button', { pressed: true }))
const pressedRow = () => document.querySelector('[aria-pressed="true"]')

function stubGeometry(tops: number[]) {
  const hs = headings()
  expect(hs.length, 'the fixture must render one node per stubbed top').toBe(tops.length)
  scroller().getBoundingClientRect = () => ({ top: 0, height: 400, bottom: 400, left: 0, right: 0, width: 700, x: 0, y: 0, toJSON: () => ({}) })
  hs.forEach((h, i) => {
    h.getBoundingClientRect = () => ({ top: tops[i], height: 24, bottom: tops[i] + 24, left: 0, right: 0, width: 600, x: 0, y: tops[i], toJSON: () => ({}) })
  })
  return hs
}

async function scrollTick() {
  await act(async () => {
    scroller().dispatchEvent(new Event('scroll'))
    await new Promise((r) => requestAnimationFrame(() => r(null)))
  })
}

afterEach(() => { vi.restoreAllMocks() })

describe('the outline is mounted in the reader', () => {
  it('renders a row per parsed heading, and the reader is not printing a second title', () => {
    renderReader(SECTIONED)
    const entries = parseOutline(SECTIONED)
    expect(entries.length, 'the fixture has to have sections at all').toBe(3)
    expect(headings().length, 'no extra item-title heading inside the scroller').toBe(entries.length)
    for (const e of entries) expect(screen.getByRole('button', { name: e.text })).toBeInTheDocument()
  })
})

describe('the active row tracks the scroll position, by rect', () => {
  it('names the section whose heading last passed the reading line', async () => {
    renderReader(SECTIONED)

    stubGeometry([50, 300, 600])
    await scrollTick()
    expect(pressedRow()?.textContent).toBe('Widgets, considered')

    stubGeometry([-200, 50, 600])
    await scrollTick()
    expect(pressedRow()?.textContent).toBe('How widgets are made')

    stubGeometry([-500, -200, 40])
    await scrollTick()
    expect(pressedRow()?.textContent).toBe('How widgets are sold')
  })

  it('marks no row while the reader is still above the first heading', async () => {
    renderReader(SECTIONED)
    stubGeometry([500, 700, 900])
    await scrollTick()
    expect(pressedRow(), 'no section is current in the preamble').toBeNull()
    expect(outlineRows().length).toBeGreaterThanOrEqual(3)
  })
})

describe('selecting a row scrolls the article to that section', () => {
  it('scrolls the heading the row NAMES, by document order', async () => {
    renderReader(SECTIONED)
    const spies = headings().map((h) => {
      const fn = vi.fn()
      ;(h as unknown as { scrollIntoView: unknown }).scrollIntoView = fn
      return fn
    })

    fireEvent.click(screen.getByRole('button', { name: 'How widgets are made' }))
    expect(spies[1], 'the second entry maps to the second heading node').toHaveBeenCalled()
    expect(spies[0], 'and to no other').not.toHaveBeenCalled()
    expect(spies[2]).not.toHaveBeenCalled()
    expect(spies[1].mock.calls[0][0]).toMatchObject({ block: 'start', behavior: 'smooth' })
  })

  it('jumps without sweeping under prefers-reduced-motion', () => {
    vi.stubGlobal('matchMedia', (q: string) => ({
      matches: q.includes('prefers-reduced-motion'), media: q, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }))
    renderReader(SECTIONED)
    const spy = vi.fn()
    ;(headings()[1] as unknown as { scrollIntoView: unknown }).scrollIntoView = spy

    fireEvent.click(screen.getByRole('button', { name: 'How widgets are made' }))
    expect(spy, 'reduced motion means instant, not absent').toHaveBeenCalled()
    expect(spy.mock.calls[0][0]).toMatchObject({ block: 'start', behavior: 'auto' })
  })
})

describe('a heading-count mismatch degrades to a no-op', () => {
  it('does not scroll to the wrong section when the renderer emitted an extra heading', () => {
    renderReader(RAW_HTML_HEADING)
    const entries = parseOutline(RAW_HTML_HEADING)
    const hs = headings()
    expect(entries.length, 'parseOutline skips the raw <h2>').toBe(2)
    expect(hs.length, 'the renderer emits it (rehype-raw)').toBe(3)

    const spies = hs.map((h) => {
      const fn = vi.fn()
      ;(h as unknown as { scrollIntoView: unknown }).scrollIntoView = fn
      return fn
    })
    const row = screen.getByRole('button', { name: 'How widgets are made' })
    fireEvent.click(row)
    for (const [i, s] of spies.entries()) {
      expect(s, `heading ${i} must not be scrolled to on a mismatched document`).not.toHaveBeenCalled()
    }
  })

  it('marks no active row on a mismatched document', async () => {
    renderReader(RAW_HTML_HEADING)
    stubGeometry([-200, 50, 600])
    await scrollTick()
    expect(pressedRow(), 'a wrong highlight is worse than no highlight').toBeNull()
    expect(outlineRows().length, 'and the rows are still there — not an empty outline').toBeGreaterThanOrEqual(2)
  })
})
