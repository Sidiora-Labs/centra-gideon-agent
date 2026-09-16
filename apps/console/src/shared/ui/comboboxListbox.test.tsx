import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Combobox } from './Combobox'


const OPTS = [
  { value: 'a', label: 'Alpha' },
  { value: 'b', label: 'Bravo' },
  { value: 'c', label: 'Charlie' },
]

function open(name: RegExp) {
  fireEvent.click(screen.getByRole('button', { name }))
}

describe('the open Combobox declares the pattern it already implemented', () => {
  it('the search field is a combobox wired to the list', () => {
    render(<Combobox options={OPTS} value="b" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Bravo/)
    const input = screen.getByRole('combobox')
    expect(input.getAttribute('aria-expanded')).toBe('true')
    expect(input.getAttribute('aria-autocomplete')).toBe('list')
    const listId = input.getAttribute('aria-controls')
    expect(listId, 'aria-controls must point at the listbox').toBeTruthy()
    expect(screen.getByRole('listbox').id).toBe(listId)
  })

  it('every row is an option, and the CHOSEN one is the selected one', () => {
    render(<Combobox options={OPTS} value="b" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Bravo/)
    const opts = screen.getAllByRole('option')
    expect(opts.map((o) => o.textContent)).toEqual(['Alpha', 'Bravo', 'Charlie'])
    expect(opts.map((o) => o.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false'])
  })

  it('publishes the keyboard cursor, and moves it on ArrowDown', () => {
    render(<Combobox options={OPTS} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    const input = screen.getByRole('combobox')
    const opts = screen.getAllByRole('option')
    expect(input.getAttribute('aria-activedescendant'), 'the cursor starts on the first row').toBe(opts[0].id)
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.getAttribute('aria-activedescendant'), 'and follows the arrow key').toBe(opts[1].id)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input.getAttribute('aria-activedescendant')).toBe(opts[0].id)
  })

  it('neither the rows nor the list are in the tab order', () => {
    render(<Combobox options={OPTS} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    for (const o of screen.getAllByRole('option')) expect(o.getAttribute('tabindex')).toBe('-1')
    expect(screen.getByRole('listbox').getAttribute('tabindex')).toBe('-1')
  })

  it('an empty result announces itself instead of being an empty listbox', () => {
    render(<Combobox options={OPTS} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="No matches" />)
    open(/Pick one/)
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zzzz' } })
    expect(screen.queryByRole('listbox'), 'no listbox when it would hold no options').toBeNull()
    expect(screen.getByRole('status').textContent).toBe('No matches')
  })

  it('the collapsed field advertises the popup it opens', () => {
    render(<Combobox options={OPTS} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    const trigger = screen.getByRole('button', { name: /Pick one/ })
    expect(trigger.getAttribute('aria-haspopup')).toBe('listbox')
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
  })
})

describe('the cursor, the announcement and Enter all index the RENDERED order', () => {
  const INTERLEAVED = [
    { value: 'a', label: 'Alpha', group: 'One' },
    { value: 'b', label: 'Bravo', group: 'Two' },
    { value: 'c', label: 'Charlie', group: 'One' },
  ]

  it('renders grouped order, not arrival order', () => {
    render(<Combobox options={INTERLEAVED} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    expect(screen.getAllByRole('option').map((o) => o.textContent)).toEqual(['Alpha', 'Charlie', 'Bravo'])
  })

  it('Enter commits the row the cursor is on', () => {
    const onChange = vi.fn()
    render(<Combobox options={INTERLEAVED} value="" onChange={onChange} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    const input = screen.getByRole('combobox')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.getAttribute('aria-activedescendant')).toBe(screen.getAllByRole('option')[1].id)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('c')
  })

  it('the group headings are groups, not options', () => {
    render(<Combobox options={INTERLEAVED} value="" onChange={vi.fn()} placeholder="Pick one" emptyText="none" />)
    open(/Pick one/)
    expect(screen.getAllByRole('group').map((g) => g.getAttribute('aria-label'))).toEqual(['One', 'Two'])
    expect(screen.getAllByRole('option').length, 'a heading must not count as a row').toBe(3)
  })
})

describe('the two behaviours jsdom cannot execute are pinned at the source', () => {
  const src = readFileSync(join(process.cwd(), "src/shared/ui/Combobox.tsx"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the active row is scrolled into view when the cursor moves', () => {
    expect(src).toMatch(/scrollIntoView\?\.\(\{ block: 'nearest' \}\)/)
    expect(src, 'and it must run when the cursor or open-state changes').toMatch(/\}, \[rowId, state\.open, ordered\]\)/)
  })

  it('hover is bound to movement, not to entering', () => {
    expect(src).toMatch(/onMouseMove=\{\(\) => dispatch\(\{ type: 'cursor', index, count: ordered\.length \}\)\}/)
    expect(src, 'mouseenter fires when scrolled content moves under a still pointer')
      .not.toMatch(/onMouseEnter=/)
  })
})
