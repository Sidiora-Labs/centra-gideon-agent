import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { AppearanceProvider } from '../../app/shell/appearance'
import { ThemeProvider, useMode } from '../../app/shell/theme'
import { TOKENS, type ColorToken, type ScalarToken, type SelectToken } from '../theme/tokenRegistry'
import { ChipInput, DateInput, Field, FieldError, NumberField, Select, TextArea, TextInput } from './forms'
import { Slider } from './Slider'
import { ColorControl, ScalarControl, SelectControl } from './TokenControls'
import { FilterMenu, type FilterSectionDef } from './FilterMenu'
import { FilterRow } from './FilterRow'
import { addChip, commitNumber, createDraft, draftReducer, rangeProgress, scalarText } from './formState'
import { filterState } from './filterState'

afterEach(() => { cleanup(); localStorage.removeItem('appearance'); localStorage.removeItem('mode') })

describe('numeric editing transitions', () => {
  it('preserves a draft on an unchanged source and replaces it on an external value change', () => {
    const edited = draftReducer(createDraft('2'), { type: 'edit', text: '' })
    expect(draftReducer(edited, { type: 'sync', value: '2' })).toBe(edited)
    expect(draftReducer(edited, { type: 'sync', value: '8' })).toEqual({ source: '8', text: '8' })
  })

  it('clamps changed commits and rejects empty or invalid drafts without losing the last value', () => {
    expect(commitNumber('-2', 5, 0, 10)).toEqual({ text: '0', value: 0, changed: true })
    expect(commitNumber('12', 5, 0, 10)).toEqual({ text: '10', value: 10, changed: true })
    expect(commitNumber('', 5)).toEqual({ text: '5', value: 5, changed: false })
    expect(commitNumber('no number', 5)).toEqual({ text: '5', value: 5, changed: false })
    expect(commitNumber('5.0', 5)).toEqual({ text: '5', value: 5, changed: false })
  })

  it('keeps a half-entered field until commit and synchronizes a later external patch', () => {
    const values: number[] = []
    const { rerender } = render(<NumberField value={3} min={1} max={8} step={0.5} ariaLabel="Attempts" onChange={(value) => values.push(value)} />)
    const field = screen.getByRole('spinbutton', { name: 'Attempts' })
    fireEvent.change(field, { target: { value: '' } })
    rerender(<NumberField value={3} min={1} max={8} step={0.5} ariaLabel="Attempts" onChange={(value) => values.push(value)} />)
    expect(field).toHaveValue(null)
    fireEvent.change(field, { target: { value: '7.5' } })
    expect(values).toEqual([])
    field.focus()
    fireEvent.keyDown(field, { key: 'Enter' })
    expect(values).toEqual([7.5])
    rerender(<NumberField value={4} ariaLabel="Attempts" onChange={(value) => values.push(value)} />)
    expect(field).toHaveValue(4)
  })
})

describe('field metadata and native control ownership', () => {
  it('associates labels and hints independently, and preserves an explicit override', () => {
    render(<><Field label="Description" hint="Explain the task"><TextArea value="" onChange={() => {}} /></Field>
      <Field label="Credentials" hint="Keep this private"><TextInput value="" onChange={() => {}} type="password" ariaLabel="API token" required /></Field>
      <Field label="Start date" hint="Local calendar"><DateInput value="2026-09-16" onChange={() => {}} /></Field></>)
    expect(screen.getByRole('textbox', { name: 'Description' })).toHaveAccessibleDescription('Explain the task')
    expect(screen.getByLabelText('API token')).toHaveAccessibleDescription('Keep this private')
    expect(screen.getByLabelText('API token')).toHaveAttribute('aria-required', 'true')
    expect(screen.getByLabelText('API token')).not.toHaveAttribute('required')
    expect(screen.getByLabelText('Start date')).toHaveAccessibleDescription('Local calendar')
  })

  it('enforces native field and option disabling even when change events are dispatched', () => {
    const changes: string[] = []
    render(<><TextInput value="locked" disabled disabledReason="Enable editing" ariaLabel="Locked text" onChange={(value) => changes.push(value)} />
      <Select value="a" ariaLabel="Choice" onChange={(value) => changes.push(value)} required
        options={[{ value: 'a', label: 'Available' }, { value: 'b', label: 'Unavailable', disabled: true, title: 'Not installed' }, { value: 'c', label: 'Ready' }]} /></>)
    const text = screen.getByRole('textbox', { name: 'Locked text' })
    expect(text).toBeDisabled()
    expect(text).toHaveAttribute('title', 'Enable editing')
    fireEvent.change(text, { target: { value: 'no' } })
    const select = screen.getByRole('combobox', { name: 'Choice' })
    expect(select).toHaveAttribute('aria-required', 'true')
    expect(screen.getByRole('option', { name: 'Unavailable' })).toHaveAttribute('title', 'Not installed')
    fireEvent.change(select, { target: { value: 'b' } })
    expect(changes).toEqual([])
    fireEvent.change(select, { target: { value: 'c' } })
    expect(changes).toEqual(['c'])
  })

  it('drops removed hints without dangling ids and announces field errors', () => {
    const { rerender } = render(<Field label="Name" hint="Unique name"><TextInput value="" onChange={() => {}} /></Field>)
    expect(screen.getByRole('textbox')).toHaveAccessibleDescription('Unique name')
    rerender(<Field label="Name"><TextInput value="" onChange={() => {}} /><FieldError>Name already exists</FieldError></Field>)
    expect(screen.getByRole('textbox')).not.toHaveAttribute('aria-describedby')
    expect(screen.getByRole('alert')).toHaveTextContent('Name already exists')
  })
})

describe('chip editing', () => {
  function Chips({ max = 2 }: { max?: number }) {
    const [values, update] = useState<string[]>(['alpha'])
    return <ChipInput values={values} onChange={update} max={max} suggestions={['alpha', 'beta', 'gamma']} ariaLabel="Tags" />
  }

  it('adds a trimmed unique chip on Enter, keeps selected suggestions out, and enforces capacity', () => {
    render(<Chips />)
    const field = screen.getByRole('combobox', { name: 'Tags' })
    expect([...document.querySelectorAll('datalist option')].map((option) => option.getAttribute('value'))).toEqual(['beta', 'gamma'])
    fireEvent.change(field, { target: { value: ' beta ' } })
    const enter = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    act(() => { field.dispatchEvent(enter) })
    expect(enter.defaultPrevented).toBe(true)
    expect(screen.getByRole('button', { name: 'Remove beta' })).toBeInTheDocument()
    fireEvent.change(field, { target: { value: 'gamma' } })
    fireEvent.keyDown(field, { key: ',' })
    expect(screen.queryByRole('button', { name: 'Remove gamma' })).toBeNull()
    expect(field).toHaveValue('')
    expect(addChip(['alpha'], 'alpha')).toBeNull()
    expect(addChip([], ' beta, ')).toEqual(['beta'])
  })

  it('removes the last chip with Backspace, commits on blur, and restores input focus after explicit removal', () => {
    render(<Chips />)
    const field = screen.getByRole('combobox', { name: 'Tags' })
    fireEvent.keyDown(field, { key: 'Backspace' })
    expect(screen.queryByRole('button', { name: 'Remove alpha' })).toBeNull()
    fireEvent.change(field, { target: { value: 'beta' } })
    fireEvent.blur(field)
    const remove = screen.getByRole('button', { name: 'Remove beta' })
    remove.focus()
    fireEvent.click(remove)
    expect(document.activeElement).toBe(field)
    expect(screen.queryByRole('button', { name: 'Remove beta' })).toBeNull()
  })

  it('routes the well into the input and leaves composition Enter uncommitted', () => {
    const { container } = render(<Chips />)
    const field = screen.getByRole('combobox', { name: 'Tags' })
    fireEvent.mouseDown(container.firstElementChild!)
    expect(document.activeElement).toBe(field)
    fireEvent.change(field, { target: { value: 'composing' } })
    fireEvent.keyDown(field, { key: 'Enter', isComposing: true })
    expect(field).toHaveValue('composing')
    expect(screen.queryByRole('button', { name: 'Remove composing' })).toBeNull()
  })
})

describe('range and token editing', () => {
  it('preserves native bounds, step, values and disabled range behavior', () => {
    const values: number[] = []
    const { rerender } = render(<Slider ariaLabel="Granularity" value={2} min={0} max={5} step={0.5} onChange={(value) => values.push(value)} />)
    const slider = screen.getByRole('slider', { name: 'Granularity' })
    expect(slider).toHaveAttribute('min', '0')
    expect(slider).toHaveAttribute('max', '5')
    expect(slider).toHaveAttribute('step', '0.5')
    fireEvent.change(slider, { target: { value: '3.5' } })
    expect(values).toEqual([3.5])
    rerender(<Slider ariaLabel="Granularity" value={2} disabled onChange={(value) => values.push(value)} />)
    fireEvent.change(slider, { target: { value: '4' } })
    expect(values).toEqual([3.5])
    expect(rangeProgress(15, 0, 10)).toBe(100)
    expect(rangeProgress(1, 1, 1)).toBe(0)
    expect(scalarText(1.25)).toBe('1.25×')
    expect(scalarText(12.7, 'px')).toBe('13px')
    expect(scalarText(0.25, 's')).toBe('0.3s')
  })

  it('edits and resets real appearance color, selection, and scalar state without submitting its enclosing form', async () => {
    const color = TOKENS.find((token) => token.varName === '--color-primary') as ColorToken
    const selection = TOKENS.find((token) => token.varName === '--font-family') as SelectToken
    const scalar = TOKENS.find((token) => token.varName === '--ui-zoom') as ScalarToken
    const submissions: string[] = []
    await act(async () => {
      render(<ThemeProvider><AppearanceProvider><form onSubmit={(event) => { event.preventDefault(); submissions.push('submitted') }}>
        <ColorControl token={color} /><SelectControl token={selection} /><ScalarControl token={scalar} />
      </form></AppearanceProvider></ThemeProvider>)
    })
    const hex = screen.getByRole('textbox', { name: `${color.label} hex value` })
    const original = (hex as HTMLInputElement).value
    fireEvent.change(hex, { target: { value: '#12' } })
    expect(hex).toHaveValue('#12')
    expect(screen.getByLabelText(`${color.label} color`)).toHaveValue(original)
    fireEvent.blur(hex)
    expect(hex).toHaveValue(original)
    fireEvent.change(hex, { target: { value: '#123456' } })
    expect(screen.getByLabelText(`${color.label} color`)).toHaveValue('#123456')
    fireEvent.click(screen.getByRole('button', { name: `Reset ${color.label}` }))
    expect(hex).toHaveValue(original)
    const choice = selection.options.find((option) => option !== selection.value)!
    fireEvent.click(screen.getByRole('button', { name: `${selection.label}: ${choice}` }))
    expect(screen.getByRole('button', { name: `${selection.label}: ${choice}` })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: `Reset ${selection.label}` }))
    expect(screen.getByRole('button', { name: `${selection.label}: ${selection.value}` })).toHaveAttribute('aria-pressed', 'true')
    const range = screen.getByRole('slider', { name: scalar.label })
    fireEvent.change(range, { target: { value: String(scalar.min) } })
    expect(range).toHaveValue(String(scalar.min))
    fireEvent.click(screen.getByRole('button', { name: `Reset ${scalar.label}` }))
    expect(range).toHaveValue(String(scalar.value))
    expect(submissions).toEqual([])
  })

  it('keeps dark and light color edits separate through the real theme provider', async () => {
    const token = TOKENS.find((entry) => entry.varName === '--color-primary') as ColorToken
    function ModeControl() { const mode = useMode(); return <button type="button" onClick={mode.toggle}>Change mode</button> }
    await act(async () => { render(<ThemeProvider><AppearanceProvider><ModeControl /><ColorControl token={token} /></AppearanceProvider></ThemeProvider>) })
    const field = screen.getByRole('textbox', { name: `${token.label} hex value` })
    fireEvent.change(field, { target: { value: '#123456' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change mode' }))
    expect(field).not.toHaveValue('#123456')
    fireEvent.change(field, { target: { value: '#abcdef' } })
    fireEvent.click(screen.getByRole('button', { name: 'Change mode' }))
    expect(field).toHaveValue('#123456')
  })
})

describe('filter selection ownership', () => {
  function Filters() {
    const [status, setStatus] = useState('all')
    const [order, setOrder] = useState('new')
    return <FilterMenu sections={[
      { title: 'Status', value: status, defaultKey: 'all', onChange: setStatus, options: [{ key: 'all', label: 'All' }, { key: 'active', label: 'Active', count: 2, groupLabel: 'Current' }] },
      { title: 'Sort', value: order, defaultKey: 'new', onChange: setOrder, options: [{ key: 'new', label: 'Newest' }, { key: 'old', label: 'Oldest' }] },
    ]} />
  }

  it('keeps selection open, counts changed dimensions, clears a section, and restores focus on Done', () => {
    const { container } = render(<Filters />)
    const trigger = screen.getByRole('button', { name: 'Filter & sort' })
    fireEvent.click(trigger)
    const status = screen.getByRole('region', { name: 'Status' })
    expect(container.contains(status)).toBe(false)
    fireEvent.click(within(status).getByRole('button', { name: 'Active2' }))
    fireEvent.click(screen.getByRole('button', { name: 'Oldest' }))
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(trigger.querySelector('[data-type="caption"]')).toHaveTextContent('2')
    expect(within(status).getByRole('button', { name: 'Active2' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(status).getByText('Clear'))
    expect(within(status).getByRole('button', { name: 'All' })).toHaveAttribute('aria-pressed', 'true')
    expect(trigger.querySelector('[data-type="caption"]')).toHaveTextContent('1')
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(document.activeElement).toBe(trigger)
  })

  it('does not cache stale section values and keeps counts and pressed state optional on standalone rows', () => {
    const sections: FilterSectionDef[] = [{ title: 'Scope', value: 'all', defaultKey: 'all', options: [], onChange: () => {} }]
    expect(filterState(sections).activeCount).toBe(0)
    sections[0].value = 'project'
    expect(filterState(sections).activeCount).toBe(1)
    render(<><FilterRow label="Zero" count={0} selected={false} onClick={() => {}} />
      <FilterRow label="Chosen" count={3} selected pressed onClick={() => {}} trailing={<span>Extra</span>} /></>)
    expect(screen.getByRole('button', { name: 'Zero' })).not.toHaveAttribute('aria-pressed')
    expect(screen.getByRole('button', { name: 'Chosen3Extra' })).toHaveAttribute('aria-pressed', 'true')
  })
})
