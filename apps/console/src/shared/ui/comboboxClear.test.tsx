import { describe, it, expect, vi } from 'vitest'
import { render, fireEvent, screen } from '@testing-library/react'
import { Combobox } from './Combobox'


const OPTS = [
  { value: '', label: 'Auto — provider default' },
  { value: 'sonnet', label: 'Claude Sonnet' },
  { value: 'opus', label: 'Claude Opus' },
]

describe('Combobox Clear is a real, reachable control', () => {
  it('is a <button> — not a role=button span — and is in the tab order', () => {
    render(<Combobox options={OPTS} value="sonnet" onChange={() => {}} />)
    const clear = screen.getByLabelText('Clear selection')
    expect(clear.tagName).toBe('BUTTON')
    expect(clear.getAttribute('tabindex')).toBeNull()
  })

  it('is NOT nested inside the field button (the nested-interactive shape)', () => {
    const { container } = render(<Combobox options={OPTS} value="sonnet" onChange={() => {}} />)
    const clear = screen.getByLabelText('Clear selection')
    const interactiveAncestors: string[] = []
    for (let el = clear.parentElement; el && el !== container; el = el.parentElement) {
      if (el.tagName === 'BUTTON' || el.tagName === 'A' || el.getAttribute('role') === 'button') {
        interactiveAncestors.push(`${el.tagName}${el.getAttribute('role') ? `[role=${el.getAttribute('role')}]` : ''}`)
      }
    }
    expect(
      interactiveAncestors,
      'Clear must not sit inside the field button — that is the nested-interactive defect',
    ).toEqual([])
  })

  it('clears the value when activated', () => {
    const onChange = vi.fn()
    render(<Combobox options={OPTS} value="sonnet" onChange={onChange} />)
    fireEvent.click(screen.getByLabelText('Clear selection'))
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('does not open the menu when Clear is activated', () => {
    render(<Combobox options={OPTS} value="sonnet" onChange={() => {}} />)
    fireEvent.click(screen.getByLabelText('Clear selection'))
    expect(screen.queryByPlaceholderText('Search…')).toBeNull()
  })
})

describe('Clear appears only when something is set', () => {
  it('is absent when the value is empty, even though an empty-valued option exists', () => {
    render(<Combobox options={OPTS} value="" onChange={() => {}} placeholder="Auto — provider default" />)
    expect(screen.getByText('Auto — provider default')).toBeTruthy()
    expect(screen.queryByLabelText('Clear selection')).toBeNull()
  })

  it('is present when a real value is set', () => {
    render(<Combobox options={OPTS} value="opus" onChange={() => {}} />)
    expect(screen.queryByLabelText('Clear selection')).not.toBeNull()
  })

  it('is absent for a value with no matching option (nothing meaningful to clear back to)', () => {
    render(<Combobox options={OPTS} value="retired-model" onChange={() => {}} />)
    expect(screen.queryByLabelText('Clear selection')).not.toBeNull()
  })
})
