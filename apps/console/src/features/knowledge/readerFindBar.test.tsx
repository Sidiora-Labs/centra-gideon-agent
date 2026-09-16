import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ReadingView, articleBlocks } from './ReadingView'
import type { KnowledgeItem } from '../../shared/data/api'


const ARTICLE = [
  '# Widgets, considered',
  '',
  'An opening paragraph with no keyword in it at all.',
  '',
  '## How widgets are made',
  '',
  'A paragraph about manufacture, mentioning widgets once.',
  '',
  '## Afterword',
  '',
  'Nothing relevant here either.',
].join('\n')

const EXPECTED_MATCHES = 3

function item(): KnowledgeItem {
  return {
    id: 'k1',
    title: 'Widgets, considered',
    content: ARTICLE,
    item_type: 'note',
    word_count: 400,
  } as KnowledgeItem
}

function renderReader() {
  return render(<ReadingView item={item()} annotations={[]} onAnnotationsChanged={() => {}} />)
}

const scroller = () => screen.getByRole('group', { name: 'Article body' })
const openFind = () => fireEvent.click(screen.getByRole('button', { name: 'Find' }))
const field = () => screen.getByLabelText('Find in article')
const counter = () => screen.getByText(/^(Match \d+ of \d+|No matches)$/)

async function search(q: string, expected: string) {
  fireEvent.change(field(), { target: { value: q } })
  await waitFor(() => expect(counter()).toHaveTextContent(expected), { timeout: 2000 })
}

afterEach(() => { vi.restoreAllMocks() })

describe('the reader hosts a find bar over its own article', () => {
  it('is not mounted until asked, and the control announces that it reveals it', () => {
    renderReader()
    expect(screen.queryByLabelText('Find in article'), 'no bar before it is opened').toBeNull()
    const control = screen.getByRole('button', { name: 'Find' })
    expect(control, 'a control that reveals content says so').toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(control)
    expect(field()).toBeInTheDocument()
    expect(control).toHaveAttribute('aria-expanded', 'true')
  })

  it('is named for THIS surface, not for the one it was born on', () => {
    renderReader()
    openFind()
    expect(field()).toBeInTheDocument()
    expect(screen.queryByLabelText('Find in conversation')).toBeNull()
  })

  it('opens on Cmd/Ctrl+F, the binding a reader already has for this', async () => {
    renderReader()
    fireEvent.keyDown(window, { key: 'f', metaKey: true })
    expect(field(), 'the reader owns ⌘F while it is open').toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Find' })).toHaveAttribute('aria-expanded', 'true')

    fireEvent.keyDown(window, { key: 'f', metaKey: true })
    expect(screen.getByRole('button', { name: 'Find' })).toHaveAttribute('aria-expanded', 'false')
    await waitFor(() => expect(screen.queryByLabelText('Find in article')).toBeNull())
  })

  it('counts the ARTICLE\'S BLOCKS — not the whole body as one, and not every block', async () => {
    renderReader()
    const blocks = articleBlocks(scroller().querySelector('.reading')!.parentElement!)
    expect(blocks.length, 'the fixture renders six blocks').toBe(6)

    openFind()
    await search('widget', `Match 1 of ${EXPECTED_MATCHES}`)
  })

  it('says so when the article does not contain the query', async () => {
    renderReader()
    openFind()
    await search('quatloos', 'No matches')
    await search('widget', `Match 1 of ${EXPECTED_MATCHES}`)
  })
})

describe('cycling matches moves the article', () => {
  it('scrolls the article\'s own block for the match it lands on', async () => {
    renderReader()
    const article = scroller().querySelector('.reading')!.parentElement!
    const spies = articleBlocks(article).map((el) => {
      const fn = vi.fn()
      ;(el as unknown as { scrollIntoView: unknown }).scrollIntoView = fn
      return fn
    })

    openFind()
    await search('widget', `Match 1 of ${EXPECTED_MATCHES}`)
    fireEvent.keyDown(field(), { key: 'ArrowDown' })
    await waitFor(() => expect(spies[2], 'the second matching block').toHaveBeenCalled())
    for (const i of [1, 4, 5]) {
      expect(spies[i], `block ${i} has no match and must not be scrolled to`).not.toHaveBeenCalled()
    }
  })
})
