import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { DocumentOutline } from './DocumentOutline'
import { parseOutline, type OutlineEntry } from './readingOutline'


const ENTRIES: OutlineEntry[] = [
  { offset: 0, depth: 0, text: 'Guide' },
  { offset: 10, depth: 1, text: 'Setup' },
  { offset: 40, depth: 2, text: 'On macOS' },
  { offset: 80, depth: 1, text: 'Setup' },
]

let scrollSpy: ReturnType<typeof vi.fn>
beforeEach(() => {
  scrollSpy = vi.fn()
  ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = scrollSpy
})
afterEach(() => {
  delete (Element.prototype as unknown as { scrollIntoView?: unknown }).scrollIntoView
})

describe('an outline with no headings renders no chrome', () => {
  it('returns nothing for an empty list', () => {
    const { container } = render(<DocumentOutline entries={[]} activeOffset={null} onSelect={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('returns nothing for a heading-less body, while a body WITH headings renders rows', () => {
    const prose = render(
      <DocumentOutline entries={parseOutline('Just prose.\n\nTwo paragraphs of it.')} activeOffset={null} onSelect={vi.fn()} />,
    )
    expect(prose.container, 'a heading-less body').toBeEmptyDOMElement()

    render(<DocumentOutline entries={parseOutline('# A\n\n## B')} activeOffset={null} onSelect={vi.fn()} />)
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['A', 'B'])
  })

  it('drops a text-less heading without dropping its neighbours', () => {
    const entries = parseOutline('# Real\n\n##\n\n## After')
    expect(entries.map((e) => e.text)).toEqual(['Real', '', 'After'])
    render(<DocumentOutline entries={entries} activeOffset={null} onSelect={vi.fn()} />)
    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual(['Real', 'After'])
  })
})

describe('every row is a real control with its heading as its name', () => {
  it('names each row, and names the panel', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={vi.fn()} />)
    expect(screen.getByRole('navigation', { name: 'Document outline' })).toBeInTheDocument()
    const rows = screen.getAllByRole('button')
    expect(rows).toHaveLength(4)
    expect(rows.map((r) => r.getAttribute('title'))).toEqual(['Guide', 'Setup', 'On macOS', 'Setup'])
  })

  it('activates by KEYBOARD, and reports the entry — not just its text', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={onSelect} />)

    await user.tab()
    await user.tab()
    expect(document.activeElement?.textContent, 'focus reached the second row').toBe('Setup')
    await user.keyboard('{Enter}')
    expect(onSelect).toHaveBeenCalledWith(ENTRIES[1])

    await user.keyboard(' ')
    expect(onSelect).toHaveBeenCalledTimes(2)
  })

  it('distinguishes the two identically-named rows by their entry', async () => {
    const onSelect = vi.fn()
    const user = userEvent.setup()
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={onSelect} />)
    const both = screen.getAllByRole('button', { name: 'Setup' })
    expect(both, 'two rows share a name — that is the case worth testing').toHaveLength(2)

    await user.click(both[1])
    expect(onSelect).toHaveBeenCalledWith(ENTRIES[3])
    expect(onSelect).not.toHaveBeenCalledWith(ENTRIES[1])
  })
})

describe('the active row is marked and kept visible', () => {
  it('exactly the active row carries the state, and the others carry its opposite', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={80} onSelect={vi.fn()} />)
    const on = screen.getAllByRole('button', { pressed: true })
    expect(on).toHaveLength(1)
    expect(on[0]).toBe(screen.getAllByRole('button', { name: 'Setup' })[1])
    expect(screen.getAllByRole('button', { pressed: false })).toHaveLength(3)
  })

  it('marks nothing when no section is active', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={vi.fn()} />)
    expect(screen.queryAllByRole('button', { pressed: true })).toHaveLength(0)
    expect(screen.getAllByRole('button'), 'the rows still rendered').toHaveLength(4)
  })

  it('scrolls the active row into view ONLY when it changes', () => {
    const { rerender } = render(<DocumentOutline entries={ENTRIES} activeOffset={10} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    expect(scrollSpy.mock.calls[0][0]).toEqual({ block: 'nearest' })

    rerender(<DocumentOutline entries={ENTRIES} activeOffset={10} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(1)

    rerender(<DocumentOutline entries={ENTRIES} activeOffset={40} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(2)

    rerender(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(2)
  })

  it('scrolls the row that OWNS the offset, not whichever row shares its text', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={80} onSelect={vi.fn()} />)
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    const scrolled = scrollSpy.mock.instances[0] as HTMLElement
    expect(scrolled.textContent).toBe('Setup')
    expect(scrolled).toBe(screen.getAllByRole('button', { name: 'Setup' })[1].closest('li'))
  })
})

describe('the rows indent from the shallowest heading present', () => {
  const indents = () =>
    screen.getAllByRole('button').map((b) => {
      const label = b.querySelector('.truncate') as HTMLElement
      return parseFloat(label.style.paddingInlineStart || '0')
    })

  it('a body whose top level is `##` renders its first level FLAT', () => {
    const entries = parseOutline(['## Setup', '', '### On macOS', '', '## Usage'].join('\n'))
    expect(entries.map((e) => e.depth)).toEqual([0, 1, 0])
    render(<DocumentOutline entries={entries} activeOffset={null} onSelect={vi.fn()} />)
    const px = indents()
    expect(px[0], 'the shallowest heading is flush').toBe(0)
    expect(px[1]).toBeGreaterThan(px[0])
    expect(px[2]).toBe(0)
  })

  it('each level in is one step further, and the step is one computed value', () => {
    render(<DocumentOutline entries={ENTRIES} activeOffset={null} onSelect={vi.fn()} />)
    const px = indents()
    expect(px[0]).toBe(0)
    expect(px[1]).toBeGreaterThan(0)
    expect(px[2]).toBe(px[1] * 2)
    expect(px[3], 'two rows at the same depth indent the same').toBe(px[1])
  })

  it('names spacing tokens that exist, and overrides the primitive with the important variant', () => {
    const SRC = join(process.cwd(), "src")
    const RAW = readFileSync(join(SRC, 'features/knowledge/DocumentOutline.tsx'), 'utf8')
    const OUTLINE = RAW.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const TOKENS = readFileSync(join(SRC, 'shared/theme/tokens.css'), 'utf8')
    const BUTTON = readFileSync(join(SRC, 'shared/ui/Button.tsx'), 'utf8')

    const scale = [...TOKENS.matchAll(/--spacing-([\w]+):/g)].map((m) => m[1])
    expect(scale, 'tokens.css defines a named spacing scale').toEqual(
      expect.arrayContaining(['xs', 's', 'm', 'l', 'xl']),
    )
    expect(scale).not.toContain('2xs')

    const gaps = [...OUTLINE.matchAll(/\bgap-([\w.]+)\b/g)].map((m) => m[1]).filter((n) => !/^[\d.]+$/.test(n))
    expect(gaps.length, 'the scan found the named gap utilities at all').toBeGreaterThan(0)
    for (const name of gaps) expect(scale, `gap-${name} must name a token that exists`).toContain(name)

    expect(BUTTON, 'the primitive really does centre — that is what is being overridden')
      .toMatch(/justify-center/)
    expect(OUTLINE).toMatch(/className="w-full !justify-start px-2"/)
  })

  it('a long heading truncates rather than widening the panel', () => {
    const long = `## ${'A very long heading that will not fit in a narrow outline panel '.repeat(3)}`
    render(<DocumentOutline entries={parseOutline(long)} activeOffset={null} onSelect={vi.fn()} />)
    const row = screen.getAllByRole('button')[0]
    const label = row.querySelector('.truncate') as HTMLElement
    expect(label, 'the label is the element that truncates').toBeTruthy()
    expect(row.getAttribute('title')).toBe(label.textContent)
    expect(row.getAttribute('title')!.length).toBeGreaterThan(60)
  })
})
