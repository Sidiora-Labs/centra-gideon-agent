import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { createRef } from 'react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { render, waitFor, within, fireEvent } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FindBar } from './FindBar'


interface Win { CSS?: { highlights?: Map<string, unknown> }; Highlight?: new (...r: Range[]) => unknown }

class FakeHighlight {
  ranges: Range[]
  constructor(...ranges: Range[]) { this.ranges = ranges }
}

function installHighlightApi() {
  const w = window as unknown as Win
  const highlights = new Map<string, unknown>()
  w.CSS = { ...(w.CSS ?? {}), highlights }
  w.Highlight = FakeHighlight as unknown as new (...r: Range[]) => unknown
  return highlights
}

function uninstallHighlightApi() {
  const w = window as unknown as Win
  delete w.CSS
  delete w.Highlight
}

const oneSegment = (s: string) => [s]

async function paintedTexts(text: string, query: string): Promise<string[]> {
  const highlights = installHighlightApi()
  const host = document.createElement('div')
  host.textContent = text
  document.body.appendChild(host)
  const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
  scrollRef.current = host

  const { container } = render(
    <FindBar items={[text]} segmentsOf={oneSegment} nodeOf={() => null}
      scrollRef={scrollRef} label="Find in page" onClose={() => {}} />,
  )
  const input = within(container).getByLabelText('Find in page')
  fireEvent.change(input, { target: { value: query } })

  let painted: string[] = []
  await waitFor(() => {
    const h = highlights.get('gideon-find') as FakeHighlight | undefined
    expect(h).toBeDefined()
    painted = (h as FakeHighlight).ranges.map((r) => r.toString())
  }, { timeout: 2000 })
  host.remove()
  return painted
}

describe('FindBar painter (#546)', () => {
  beforeEach(() => { vi.spyOn(console, 'error').mockImplementation(() => {}) })
  afterEach(() => { uninstallHighlightApi(); vi.restoreAllMocks() })

  it('highlights the right characters when İ (U+0130) precedes the match', async () => {
    expect(await paintedTexts('İİİİtarget', 'target')).toEqual(['target'])
    expect(await paintedTexts('İtarget', 'target')).toEqual(['target'])
  })

  it('paints every occurrence instead of blanking the page on one bad node', async () => {
    expect(await paintedTexts('İtarget and target again', 'target')).toEqual(['target', 'target'])
  })

  it('paints the correct span mid-text (the silent off-by-one case)', async () => {
    expect(await paintedTexts('İzmir kiln target 1240C', 'target')).toEqual(['target'])
  })

  it('still paints plain ASCII matches case-insensitively', async () => {
    expect(await paintedTexts('Docker Compose and docker again', 'DOCKER')).toEqual(['Docker', 'docker'])
  })
})


describe('FindBar is surface-agnostic', () => {
  const SRC = join(process.cwd(), "src")
  const src = readFileSync(join(SRC, 'shared/ui/FindBar.tsx'), 'utf8')
  const PAGES_IMPORT = /^\s*import[^\n]*from\s*'[^']*\bpages\//m

  it('imports nothing from pages/ — a shared primitive cannot depend on one caller', () => {
    expect(PAGES_IMPORT.test(src), 'shared/ui/FindBar.tsx must not import from pages/').toBe(false)
  })

  it('the import rail is not vacuous — it sees this file\'s real imports, and would flag one', () => {
    expect(src, 'the file under test must actually have imports').toMatch(
      /^import \{ findInText, matchingIndices \} from '\.\/findText'$/m)
    expect(PAGES_IMPORT.test("import { ChatTurn } from '../../features/chat/chatTypes'"), 'positive control')
      .toBe(true)
    expect(PAGES_IMPORT.test("import { spring } from '../theme/motion'"), 'negative control').toBe(false)
  })

  it('counts and cycles ITEMS the host defines, and scrolls the one it is on', async () => {
    const rows = [
      { heading: 'Kiln schedule', body: 'cone 6 target 1240C' },
      { heading: 'Glaze notes', body: 'nothing to see' },
      { heading: 'Target list', body: 'and another target' },
    ]
    const nodes = rows.map(() => {
      const el = document.createElement('div')
      el.scrollIntoView = vi.fn()
      return el
    })
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    const { container } = render(
      <FindBar items={rows} segmentsOf={(r) => [r.heading, r.body]} nodeOf={(_r, i) => nodes[i]}
        scrollRef={scrollRef} label="Find in article" onClose={() => {}} />,
    )
    const input = within(container).getByLabelText('Find in article')
    fireEvent.change(input, { target: { value: 'target' } })

    await waitFor(() => expect(within(container).getByText('1/2')).toBeTruthy())

    await userEvent.click(within(container).getByLabelText('Next match'))
    expect(within(container).getByText('2/2')).toBeTruthy()
    expect(nodes[2].scrollIntoView, 'the second stop is row 2, not row 1').toHaveBeenCalled()
    expect(nodes[1].scrollIntoView, 'row 1 matches nothing and is never a stop').not.toHaveBeenCalled()

    await userEvent.click(within(container).getByLabelText('Next match'))
    expect(within(container).getByText('1/2')).toBeTruthy()
    expect(nodes[0].scrollIntoView).toHaveBeenCalled()
  })

  it('says "No matches" through the host\'s own vocabulary, with nothing chat-shaped mounted', async () => {
    const scrollRef = createRef<HTMLDivElement>() as React.MutableRefObject<HTMLDivElement | null>
    const { container } = render(
      <FindBar items={['a paragraph of prose']} segmentsOf={oneSegment} nodeOf={() => null}
        scrollRef={scrollRef} label="Find in article" onClose={() => {}} />,
    )
    expect(within(container).queryByLabelText('Find in conversation')).toBeNull()
    const input = within(container).getByLabelText('Find in article')
    expect(input.getAttribute('placeholder')).toBe('Find in article')

    fireEvent.change(input, { target: { value: 'zebra' } })
    await waitFor(() => expect(within(container).getByRole('status').textContent).toBe('No matches'))
    fireEvent.change(input, { target: { value: 'prose' } })
    await waitFor(() => expect(within(container).getByRole('status').textContent).toBe('Match 1 of 1'))
  })
})
