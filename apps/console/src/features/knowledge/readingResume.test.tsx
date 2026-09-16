import { beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { ReadingView } from './ReadingView'
import { api, type KnowledgeItem } from '../../shared/data/api'
import { getReadingPosition, setReadingPosition } from './readingPosition'


const KEY = 'knowledge-reading-positions'
const LONG_ARTICLE = [
  '# On long articles', '',
  'A paragraph long enough that the reader has somewhere to scroll to.', '',
  '## A second section', '',
  'Another paragraph, so the outline has two rows and the body has height.',
].join('\n')

function item(over: Partial<KnowledgeItem> = {}): KnowledgeItem {
  return { id: 'k1', title: 'On long articles', content: LONG_ARTICLE, item_type: 'note', word_count: 440, ...over } as KnowledgeItem
}

function stubScroll(el: HTMLElement, scrollTop: number, scrollHeight = 2000, clientHeight = 500) {
  Object.defineProperty(el, 'scrollTop', { value: scrollTop, writable: true, configurable: true })
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true })
}

const article = () => screen.getByRole('group', { name: 'Article body' })

async function settle(ms = 500) {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)) })
}

async function scrollTo(region: HTMLElement, top: number) {
  stubScroll(region, top)
  await act(async () => { region.dispatchEvent(new Event('scroll')) })
  await settle(50)
  await settle()
}

function mount(over: Partial<KnowledgeItem> = {}) {
  render(<ReadingView item={item(over)} annotations={[]} onAnnotationsChanged={() => {}} />)
  return article()
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
  vi.spyOn(api, 'setKnowledgeReadState').mockResolvedValue({ ok: true, read_state: 'reading' })
  if (!Range.prototype.getBoundingClientRect) {
    Range.prototype.getBoundingClientRect = () =>
      ({ x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}) }) as DOMRect
  }
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: false, media: q, onchange: null,
    addListener: () => {}, removeListener: () => {}, addEventListener: () => {},
    removeEventListener: () => {}, dispatchEvent: () => false,
  }))
})

describe('the reader persists where you stopped', () => {
  it('writes the fraction after the scroll settles', async () => {
    const region = mount()
    stubScroll(region, 0)
    await settle()
    expect(getReadingPosition('k1'), 'the top of an article is not a place to resume').toBeNull()

    await scrollTo(region, 750)

    expect(getReadingPosition('k1')?.pct).toBeCloseTo(0.5, 2)
  })

  it('restores the saved position before the first write can erase it', async () => {
    setReadingPosition('k1', 0.5)
    const region = mount()
    stubScroll(region, 0)
    await settle()

    expect(region.scrollTop).toBe(750)
    expect(getReadingPosition('k1')?.pct).toBeCloseTo(0.5, 2)
    expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toHaveAttribute('aria-valuenow', '50')
  })

  it('forgets the position once the article is finished', async () => {
    setReadingPosition('k1', 0.5)
    const region = mount()
    stubScroll(region, 0)
    await settle()

    await scrollTo(region, 1500)

    expect(getReadingPosition('k1'), 'a finished article has nothing left to resume to').toBeNull()
  })

  it('survives a corrupted store rather than throwing inside a render', async () => {
    localStorage.setItem(KEY, '{not json')
    const region = mount()
    stubScroll(region, 0)
    await settle()
    expect(region.scrollTop).toBe(0)
    expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toBeInTheDocument()
  })
})

describe('opening the reader is what puts an item on the shelf', () => {
  it('marks an UNREAD item as reading once the reader actually scrolls', async () => {
    const region = mount({ read_state: 'unread' })
    stubScroll(region, 0)
    await settle(50)
    expect(api.setKnowledgeReadState, 'the top of the page is not reading it').not.toHaveBeenCalled()

    await scrollTo(region, 300)

    expect(api.setKnowledgeReadState).toHaveBeenCalledWith('k1', 'reading')
  })

  it('never demotes a FINISHED item back onto the shelf', async () => {
    const region = mount({ read_state: 'read' })
    await scrollTo(region, 300)

    expect(api.setKnowledgeReadState).not.toHaveBeenCalled()
  })

  it('writes the state once, not once per scroll frame', async () => {
    const region = mount({ read_state: 'unread' })
    for (const top of [300, 400, 500]) await scrollTo(region, top)
    expect(vi.mocked(api.setKnowledgeReadState).mock.calls).toHaveLength(1)
  })
})
