import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChipInput, DateInput, Field, NumberField, Select, TextArea, TextInput } from './forms'

const longWord = 'extremelylongunbrokenconfigurationidentifierthathastowrap'

function hasClasses(element: Element | null, ...tokens: string[]) {
  expect(element).not.toBeNull()
  for (const token of tokens) expect(element).toHaveClass(token)
}

describe('narrow form controls', () => {
  it('keeps a long label and right action in a wrapping header with an accessible field name', () => {
    const action = vi.fn()
    const change = vi.fn()
    const { container } = render(
      <div className="w-[240px]">
        <Field label={longWord} right={<button type="button" onClick={action}>Reset setting</button>} hint="A long setting remains visible">
          <TextInput value="" onChange={change} />
        </Field>
      </div>,
    )
    const label = screen.getByText(longWord)
    const header = label.parentElement!
    hasClasses(header, 'flex-wrap', 'min-w-0')
    hasClasses(label, 'min-w-0', '[overflow-wrap:anywhere]')
    hasClasses(screen.getByRole('button', { name: 'Reset setting' }).parentElement, 'max-w-full')
    const input = container.querySelector('input')!
    expect(input.getAttribute('aria-labelledby')).toBe(label.id)
    fireEvent.change(input, { target: { value: 'retained value' } })
    expect(change).toHaveBeenCalledWith('retained value')
    fireEvent.click(screen.getByRole('button', { name: 'Reset setting' }))
    expect(action).toHaveBeenCalledOnce()
  })

  it('caps text input chrome and icon wrapper without dropping a trailing action', () => {
    const change = vi.fn()
    const show = vi.fn()
    const { container } = render(
      <div className="w-[220px]">
        <TextInput value="" onChange={change} leadingIcon={<span>🔎</span>}
          trailingSlot={<button type="button" onClick={show}>Show</button>} />
      </div>,
    )
    const input = container.querySelector('input')!
    hasClasses(input, 'w-full', 'min-w-0', 'max-w-full')
    hasClasses(input.parentElement, 'min-w-0', 'max-w-full')
    fireEvent.change(input, { target: { value: longWord } })
    expect(change).toHaveBeenCalledWith(longWord)
    fireEvent.click(screen.getByRole('button', { name: 'Show' }))
    expect(show).toHaveBeenCalledOnce()
  })

  it('keeps a disabled text field capped and rejects user typing', async () => {
    const change = vi.fn()
    const { container } = render(
      <div className="w-[220px]"><TextInput value={longWord} onChange={change}
        disabled disabledReason="Locked by policy" /></div>,
    )
    const input = container.querySelector('input')!
    hasClasses(input, 'min-w-0', 'max-w-full')
    expect(input).toBeDisabled()
    expect(input).toHaveAttribute('title', 'Locked by policy')
    await userEvent.type(input, 'different')
    expect(input).toHaveValue(longWord)
    expect(change).not.toHaveBeenCalled()
  })

  it('keeps monospaced text chrome within the same narrow boundary', () => {
    const { container } = render(
      <div className="w-[220px]"><TextInput value={longWord} onChange={() => {}} mono /></div>,
    )
    const input = container.querySelector('input')!
    hasClasses(input, 'min-w-0', 'max-w-full', 'font-mono')
  })

  it('caps a long textarea while retaining editing and its disabled state', () => {
    const change = vi.fn()
    const { container, rerender } = render(
      <div className="w-[220px]"><TextArea value={longWord} onChange={change} /></div>,
    )
    const textarea = container.querySelector('textarea')!
    const chrome = textarea.classList.contains('w-full') ? textarea : textarea.parentElement
    hasClasses(chrome, 'w-full', 'min-w-0', 'max-w-full', 'resize-y')
    fireEvent.change(textarea, { target: { value: 'updated' } })
    expect(change).toHaveBeenCalledWith('updated')
    rerender(<div className="w-[220px]"><TextArea value={longWord} onChange={change}
      disabled disabledReason="Read only" /></div>)
    expect(textarea).toBeDisabled()
    expect(textarea).toHaveAttribute('title', 'Read only')
  })

  it('caps a monospaced textarea in both native and SDK chrome', () => {
    const { container } = render(
      <div className="w-[220px]"><TextArea value={longWord} onChange={() => {}} mono /></div>,
    )
    const textarea = container.querySelector('textarea')!
    const chrome = textarea.classList.contains('w-full') ? textarea : textarea.parentElement
    hasClasses(chrome, 'w-full', 'min-w-0', 'max-w-full', 'font-mono')
  })

  it('caps a custom-width number control and still commits a clamped value', () => {
    const change = vi.fn()
    const { container } = render(
      <div className="w-[220px]"><Field label="Retention days">
        <NumberField value={4} min={0} max={10} width="w-full" onChange={change} />
      </Field></div>,
    )
    const input = container.querySelector('input')!
    hasClasses(input, 'w-full', 'min-w-0', 'max-w-full')
    expect(input).toHaveAttribute('type', 'number')
    fireEvent.change(input, { target: { value: '42' } })
    fireEvent.blur(input)
    expect(change).toHaveBeenCalledWith(10)
  })

  it('caps the native date input and preserves date changes', () => {
    const change = vi.fn()
    const { container } = render(
      <div className="w-[220px]"><Field label="Audit date">
        <DateInput value="2026-09-01" onChange={change} />
      </Field></div>,
    )
    const input = container.querySelector('input')!
    hasClasses(input, 'w-full', 'min-w-0', 'max-w-full')
    expect(input).toHaveAttribute('type', 'date')
    fireEvent.change(input, { target: { value: '2026-09-26' } })
    expect(change).toHaveBeenCalledWith('2026-09-26')
  })

  it('caps a select and its arrow wrapper without losing option selection', () => {
    const change = vi.fn()
    const { container } = render(
      <div className="w-[220px]"><Field label="Deployment region">
        <Select value="a" onChange={change} options={[
          { value: 'a', label: longWord },
          { value: 'b', label: 'Europe' },
          { value: 'c', label: 'Unavailable', disabled: true },
        ]} />
      </Field></div>,
    )
    const select = container.querySelector('select')!
    hasClasses(select, 'w-full', 'min-w-0', 'max-w-full')
    hasClasses(select.parentElement, 'min-w-0', 'max-w-full')
    fireEvent.change(select, { target: { value: 'b' } })
    expect(change).toHaveBeenCalledWith('b')
    expect(container.querySelector('option[value="c"]')).toBeDisabled()
  })

  it('retains the select disabled state under the same width contract', () => {
    const change = vi.fn()
    const { container, rerender } = render(
      <div className="w-[220px]"><Select value="a" onChange={change} disabled disabledReason="Unavailable"
        options={[{ value: 'a', label: longWord }, { value: 'b', label: 'Next' }]} /></div>,
    )
    const select = container.querySelector('select')!
    hasClasses(select, 'min-w-0', 'max-w-full')
    expect(select).toBeDisabled()
    expect(select).toHaveAttribute('title', 'Unavailable')
    fireEvent.change(select, { target: { value: 'b' } })
    expect(change).not.toHaveBeenCalled()
    rerender(<div className="w-[220px]"><Select value="a" onChange={change} disabled
      options={[{ value: 'a', label: longWord }, { value: 'b', label: 'Next' }]} /></div>)
    expect(select).not.toHaveAttribute('title')
  })

  it('allows an unbroken chip to wrap beside a removable action', () => {
    const change = vi.fn()
    const { container } = render(
      <div className="w-[220px]"><ChipInput values={[longWord]} onChange={change} /></div>,
    )
    const shell = container.querySelector('[aria-disabled]') ?? container.querySelector('input')!.parentElement!
    hasClasses(shell, 'flex-wrap', 'min-w-0', 'max-w-full')
    const chipText = screen.getByText(longWord)
    hasClasses(chipText, 'min-w-0', '[overflow-wrap:anywhere]')
    hasClasses(chipText.parentElement, 'max-w-full', 'min-h-7')
    const input = container.querySelector('input')!
    hasClasses(input, 'min-w-0', 'basis-20')
    fireEvent.click(screen.getByRole('button', { name: `Remove ${longWord}` }))
    expect(change).toHaveBeenCalledWith([])
    expect(document.activeElement).toBe(input)
  })

  it('keeps a disabled long chip visible but non-removable', () => {
    const change = vi.fn()
    const { container } = render(
      <div className="w-[220px]"><ChipInput values={[longWord]} onChange={change}
        disabled disabledReason="Managed" /></div>,
    )
    const chipText = screen.getByText(longWord)
    hasClasses(chipText, '[overflow-wrap:anywhere]')
    const button = screen.getByRole('button', { name: `Remove ${longWord}` })
    expect(button).toBeDisabled()
    expect(container.querySelector('input')).toBeDisabled()
    fireEvent.click(button)
    expect(change).not.toHaveBeenCalled()
  })

  it('focuses the chip editor from empty shell space and preserves keyboard insertion', () => {
    const change = vi.fn()
    const { container } = render(
      <div className="w-[220px]"><ChipInput values={[]} onChange={change}
        suggestions={['suggested']} /></div>,
    )
    const input = container.querySelector('input')!
    const shell = input.parentElement!
    hasClasses(shell, 'min-w-0', 'max-w-full')
    expect(container.querySelector('datalist option[value="suggested"]')).not.toBeNull()
    fireEvent.mouseDown(shell)
    expect(document.activeElement).toBe(input)
    fireEvent.change(input, { target: { value: 'new-tag' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(change).toHaveBeenCalledWith(['new-tag'])
  })
})
