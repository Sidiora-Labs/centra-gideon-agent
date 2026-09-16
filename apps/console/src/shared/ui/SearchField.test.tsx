import { describe, it, expect, vi } from 'vitest'
import { createRef } from 'react'
import { render, fireEvent } from '@testing-library/react'
import { SearchField } from './SearchField'


describe('SearchField', () => {
  it('renders a named type=search input (label falls back to placeholder)', () => {
    const { getByRole } = render(<SearchField value="" onChange={() => {}} placeholder="Search tasks" />)
    const input = getByRole('searchbox', { name: 'Search tasks' })
    expect(input).toHaveAttribute('type', 'search')
  })

  it('prefers an explicit ariaLabel over the placeholder', () => {
    const { getByRole } = render(<SearchField value="" onChange={() => {}} placeholder="Filter…" ariaLabel="Search chats" />)
    expect(getByRole('searchbox', { name: 'Search chats' })).toBeInTheDocument()
  })

  it('shows the clear-X only when there is a value, and clears on click', () => {
    const onChange = vi.fn()
    const { queryByRole, rerender, getByRole } = render(<SearchField value="" onChange={onChange} placeholder="Search" />)
    expect(queryByRole('button', { name: /clear/i })).toBeNull()
    rerender(<SearchField value="hi" onChange={onChange} placeholder="Search" />)
    fireEvent.click(getByRole('button', { name: /clear/i }))
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('clears on Escape (overlay) when there is a value', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<SearchField value="hi" onChange={onChange} placeholder="Search" />)
    fireEvent.keyDown(getByRole('searchbox'), { key: 'Escape' })
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('does NOT clear on Escape by default in the inline variant', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<SearchField variant="inline" value="hi" onChange={onChange} placeholder="Search" />)
    fireEvent.keyDown(getByRole('searchbox'), { key: 'Escape' })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('clears on Escape in the inline variant when clearOnEscape is set', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<SearchField variant="inline" clearOnEscape value="hi" onChange={onChange} placeholder="Search" />)
    fireEvent.keyDown(getByRole('searchbox'), { key: 'Escape' })
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('runs the caller onKeyDown first and lets it pre-empt the built-in Escape', () => {
    const onChange = vi.fn()
    const onKeyDown = vi.fn((e) => e.preventDefault())
    const { getByRole } = render(<SearchField value="hi" onChange={onChange} placeholder="Search" onKeyDown={onKeyDown} />)
    fireEvent.keyDown(getByRole('searchbox'), { key: 'Escape' })
    expect(onKeyDown).toHaveBeenCalledTimes(1)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('forwards typing through onChange', () => {
    const onChange = vi.fn()
    const { getByRole } = render(<SearchField value="" onChange={onChange} placeholder="Search" />)
    fireEvent.change(getByRole('searchbox'), { target: { value: 'abc' } })
    expect(onChange).toHaveBeenCalledWith('abc')
  })

  it('omits the clear-X when clearable is false, even with a value', () => {
    const { queryByRole } = render(<SearchField variant="inline" clearable={false} value="hi" onChange={() => {}} placeholder="Search" />)
    expect(queryByRole('button', { name: /clear/i })).toBeNull()
  })

  it('renders a caller trailing slot after the clear-X (inline)', () => {
    const { getByText } = render(
      <SearchField variant="inline" value="" onChange={() => {}} placeholder="Search"
        trailingSlot={<kbd>esc</kbd>} />,
    )
    expect(getByText('esc')).toBeInTheDocument()
  })

  it('forwards inputRef to the underlying input', () => {
    const ref = createRef<HTMLInputElement>()
    render(<SearchField value="" onChange={() => {}} placeholder="Search" inputRef={ref} />)
    expect(ref.current?.tagName).toBe('INPUT')
  })

  it('applies the inset focus ring only in the overlay variant', () => {
    const { getByRole, rerender } = render(<SearchField value="" onChange={() => {}} placeholder="Search" />)
    expect(getByRole('searchbox').className).toContain('focus:ring-inset')
    rerender(<SearchField variant="inline" value="" onChange={() => {}} placeholder="Search" />)
    expect(getByRole('searchbox').className).not.toContain('focus:ring')
  })

  it('forwards the popup trio, and aria-expanded with it', () => {
    const { getByRole } = render(
      <SearchField variant="inline" value="x" onChange={() => {}} ariaLabel="Find file by name"
        ariaHasPopup="listbox" ariaControls="qo-list" ariaActiveDescendant="qo-opt-2" ariaExpanded />,
    )
    const input = getByRole('searchbox')
    expect(input.getAttribute('aria-haspopup')).toBe('listbox')
    expect(input.getAttribute('aria-controls')).toBe('qo-list')
    expect(input.getAttribute('aria-activedescendant')).toBe('qo-opt-2')
    expect(input.getAttribute('aria-expanded'), 'the attribute added for a popup that toggles').toBe('true')
  })

  it('says expanded=false rather than omitting it while a popup exists but is closed', () => {
    const { getByRole } = render(
      <SearchField variant="inline" value="x" onChange={() => {}} ariaLabel="Find file by name"
        ariaHasPopup="listbox" ariaControls="qo-list" ariaExpanded={false} />,
    )
    expect(getByRole('searchbox').getAttribute('aria-expanded')).toBe('false')
  })

  it('omits it entirely for a field with no popup at all', () => {
    const { getByRole } = render(<SearchField value="" onChange={() => {}} placeholder="Search" />)
    expect(getByRole('searchbox').hasAttribute('aria-expanded'),
      'a plain search box must not claim to control anything').toBe(false)
  })

  it('suppresses the native webkit search-cancel glyph in both variants', () => {
    const { getByRole, rerender } = render(<SearchField value="" onChange={() => {}} placeholder="Search" />)
    expect(getByRole('searchbox').className).toContain('[&::-webkit-search-cancel-button]:hidden')
    rerender(<SearchField variant="inline" value="" onChange={() => {}} placeholder="Search" />)
    expect(getByRole('searchbox').className).toContain('[&::-webkit-search-cancel-button]:hidden')
  })
})
