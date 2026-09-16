import { useState } from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent } from '@testing-library/react'
import { VariableRow } from './VariableRow'
import type { PromptVariable } from '../../shared/data/api'

const base: PromptVariable = { name: 'topic', type: 'text', description: '', required: false }

function Row({ initial, onCommit }: { initial: PromptVariable; onCommit?: (v: PromptVariable) => void }) {
  const [v, setV] = useState(initial)
  return (
    <VariableRow v={v} onRemove={() => {}}
      onChange={(patch) => setV((prev) => { const next = { ...prev, ...patch }; onCommit?.(next); return next })} />
  )
}

describe('VariableRow', () => {
  it('renders name/type/required/default with accessible names + autofill-defeating names', () => {
    const { getByLabelText, getByText } = render(
      <VariableRow v={base} onChange={() => {}} onRemove={() => {}} />,
    )
    expect((getByLabelText('Name of variable "topic"') as HTMLInputElement).value).toBe('topic')
    expect(getByLabelText('Type of variable "topic"')).toBeTruthy()
    expect(getByLabelText('Description of variable "topic"')).toBeTruthy()
    expect(getByLabelText('Default value of variable "topic"')).toBeTruthy()
    expect((getByLabelText('Name of variable "topic"') as HTMLInputElement).name).toMatch(/^var-name-/)
    expect((getByLabelText('Type of variable "topic"') as HTMLSelectElement).name).toMatch(/^var-type-/)
    expect(getByText('optional')).toBeTruthy()
  })

  it('defaults the description placeholder to the prompt wording; overrides for snippets', () => {
    const { getByLabelText, rerender } = render(
      <VariableRow v={base} onChange={() => {}} onRemove={() => {}} />,
    )
    expect((getByLabelText('Description of variable "topic"') as HTMLInputElement).placeholder).toBe('Description (shown when invoked)')
    rerender(<VariableRow v={base} onChange={() => {}} onRemove={() => {}} descriptionPlaceholder="Description" />)
    expect((getByLabelText('Description of variable "topic"') as HTMLInputElement).placeholder).toBe('Description')
  })

  it('reveals the choices field only for select-type variables', () => {
    const { queryByLabelText, rerender } = render(
      <VariableRow v={base} onChange={() => {}} onRemove={() => {}} />,
    )
    expect(queryByLabelText('Choices for variable "topic"')).toBeNull()
    rerender(<VariableRow v={{ ...base, type: 'select', options: ['a', 'b'] }} onChange={() => {}} onRemove={() => {}} />)
    expect((queryByLabelText('Choices for variable "topic"') as HTMLInputElement).value).toBe('a, b')
  })

  it('sanitizes the name, toggles required, and wires remove', () => {
    const onChange = vi.fn()
    const onRemove = vi.fn()
    const { getByLabelText, getByText, container } = render(
      <VariableRow v={base} onChange={onChange} onRemove={onRemove} />,
    )
    const nameInput = getByLabelText('Name of variable "topic"') as HTMLInputElement
    fireEvent.change(nameInput, { target: { value: 'my var!' } })
    expect(onChange).toHaveBeenCalledWith({ name: 'my_var_' })

    getByText('optional').click()
    expect(onChange).toHaveBeenCalledWith({ required: true })

    const buttons = container.querySelectorAll('button')
    ;(buttons[buttons.length - 1] as HTMLButtonElement).click()
    expect(onRemove).toHaveBeenCalledTimes(1)
  })

  const type = (input: HTMLInputElement, text: string) => {
    for (const ch of text) fireEvent.change(input, { target: { value: input.value + ch } })
  }

  it('keeps a comma while typing, so a select can hold more than one option', () => {
    const onCommit = vi.fn()
    const { getByLabelText } = render(<Row initial={{ ...base, type: 'select', options: [] }} onCommit={onCommit} />)
    const input = getByLabelText('Choices for variable "topic"') as HTMLInputElement
    type(input, 'red,green')
    expect(input.value).toBe('red,green')
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledWith(expect.objectContaining({ options: ['red', 'green'] }))
  })

  it('lets a space follow a comma, and trims it away only on commit', () => {
    const onCommit = vi.fn()
    const { getByLabelText } = render(<Row initial={{ ...base, type: 'select', options: [] }} onCommit={onCommit} />)
    const input = getByLabelText('Choices for variable "topic"') as HTMLInputElement
    type(input, 'red, green, blue')
    expect(input.value).toBe('red, green, blue')
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledWith(expect.objectContaining({ options: ['red', 'green', 'blue'] }))
  })

  it('drops empty choices on commit (a trailing comma persists no blank option)', () => {
    const onCommit = vi.fn()
    const { getByLabelText } = render(<Row initial={{ ...base, type: 'select', options: [] }} onCommit={onCommit} />)
    const input = getByLabelText('Choices for variable "topic"') as HTMLInputElement
    type(input, 'red, ,green,')
    fireEvent.blur(input)
    expect(onCommit).toHaveBeenCalledWith(expect.objectContaining({ options: ['red', 'green'] }))
  })

  it('re-syncs the field when options change from outside the row', () => {
    const { getByLabelText, rerender } = render(
      <VariableRow v={{ ...base, type: 'select', options: ['a', 'b'] }} onChange={() => {}} onRemove={() => {}} />,
    )
    expect((getByLabelText('Choices for variable "topic"') as HTMLInputElement).value).toBe('a, b')
    rerender(<VariableRow v={{ ...base, type: 'select', options: ['x'] }} onChange={() => {}} onRemove={() => {}} />)
    expect((getByLabelText('Choices for variable "topic"') as HTMLInputElement).value).toBe('x')
  })
})
