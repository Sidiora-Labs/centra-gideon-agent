import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { Modal } from './Modal'
import { SidePanel } from './SidePanel'
import { Combobox } from './Combobox'
import { SearchField } from './SearchField'
import { ShortcutRecorder } from './ShortcutRecorder'
import { choiceProjection, choiceReducer, closedChoice } from './interactionState'

const choices = [
  { value: 'first', label: 'First', group: 'One', description: 'North' },
  { value: 'second', label: 'Second', group: 'Two' },
  { value: 'third', label: 'Third', group: 'One' },
]

describe('selection controller', () => {
  it('projects the same grouped order for rendering and keyboard selection', () => {
    expect(choiceProjection(choices, '').ordered.map((row) => row.value)).toEqual(['first', 'third', 'second'])
    expect(choiceProjection(choices, '  NORTH ').ordered.map((row) => row.value)).toEqual(['first'])
    expect(choiceProjection(choices, 'Two').ordered.map((row) => row.value)).toEqual(['second'])
  })

  it('never creates a negative cursor on an empty result and resets when reopening', () => {
    const opened = choiceReducer(closedChoice, { type: 'open' })
    const searched = choiceReducer(opened, { type: 'query', value: 'missing' })
    expect(choiceReducer(searched, { type: 'cursor', index: 1, count: 0 }).cursor).toBe(0)
    expect(choiceReducer(searched, { type: 'open' })).toEqual(opened)
  })

  it('clamps an existing cursor when options change and commits the visible row', () => {
    const saved: string[] = []
    const { rerender } = render(<Combobox options={choices} value="" onChange={(value) => saved.push(value)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Select…' }))
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' })
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' })
    rerender(<Combobox options={[choices[0]]} value="" onChange={(value) => saved.push(value)} />)
    const input = screen.getByRole('combobox')
    expect(input.getAttribute('aria-activedescendant')).toBe(screen.getByRole('option').id)
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(saved).toEqual(['first'])
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Select…' }))
  })

  it('mouse movement changes the cursor and blur/outside click close the popup', () => {
    render(<><Combobox options={choices} value="" onChange={() => {}} /><button>Outside</button></>)
    const open = () => fireEvent.click(screen.getByRole('button', { name: 'Select…' }))
    open()
    const last = screen.getAllByRole('option')[2]
    fireEvent.mouseMove(last)
    expect(screen.getByRole('combobox').getAttribute('aria-activedescendant')).toBe(last.id)
    fireEvent.blur(screen.getByRole('combobox'), { relatedTarget: screen.getByText('Outside') })
    expect(screen.queryByRole('combobox')).toBeNull()
    open()
    fireEvent.mouseDown(screen.getByText('Outside'))
    expect(screen.queryByRole('combobox')).toBeNull()
  })
})

describe('overlay interaction ownership', () => {
  it('names composed titles and closes only the innermost modal', () => {
    const closed: string[] = []
    function Host() {
      const [inner, showInner] = useState(true)
      return <Modal title={<span>Outer <b>workspace</b></span>} onClose={() => closed.push('outer')}>
        <button>Outer action</button>
        {inner && <Modal title="Inner" onClose={() => { closed.push('inner'); showInner(false) }}>Inner body</Modal>}
      </Modal>
    }
    render(<Host />)
    expect(screen.getByRole('dialog', { name: 'Outer workspace' })).toBeTruthy()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(closed).toEqual(['inner'])
    expect(screen.queryByRole('dialog', { name: 'Inner' })).toBeNull()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(closed).toEqual(['inner', 'outer'])
  })

  it('lets the combobox consume Escape before its modal and restores its trigger', () => {
    const closed: string[] = []
    render(<Modal title="Pick" onClose={() => closed.push('modal')}>
      <Combobox options={choices} value="" onChange={() => {}} />
    </Modal>)
    fireEvent.click(screen.getByRole('button', { name: 'Select…' }))
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' })
    expect(closed).toEqual([])
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Select…' }))
    fireEvent.keyDown(document.activeElement!, { key: 'Escape' })
    expect(closed).toEqual(['modal'])
  })

  it('preserves focus restoration and scrim dismissal', () => {
    const calls: string[] = []
    function Host() {
      const [open, setOpen] = useState(false)
      return <><button onClick={() => setOpen(true)}>Launch</button>
        {open && <Modal title="Details" onClose={() => { calls.push('close'); setOpen(false) }}><button>Action</button></Modal>}
      </>
    }
    render(<Host />)
    const launch = screen.getByText('Launch')
    launch.focus()
    fireEvent.click(launch)
    fireEvent.click(screen.getByRole('dialog').previousElementSibling!)
    expect(calls).toEqual(['close'])
    expect(document.activeElement).toBe(launch)
  })
})

describe('panel modes and keyboard resize', () => {
  it('resizes with the splitter, preserves the exact storage key and clears URL state on close', () => {
    const key = 'interaction-panel-width'
    localStorage.setItem(key, '500')
    const events: unknown[] = []
    const { unmount } = render(<SidePanel title="Inspector" storeKey={key} onClose={() => events.push('closed')}
      urlKey={{ key: 'inspect', setQuery: (patch) => events.push(patch) }}>Content</SidePanel>)
    const separator = screen.getByRole('separator')
    expect(separator).toHaveAttribute('aria-valuenow', '500')
    fireEvent.keyDown(separator, { key: 'ArrowLeft' })
    expect(separator).toHaveAttribute('aria-valuenow', '516')
    fireEvent.keyDown(separator, { key: 'End' })
    expect(separator).toHaveAttribute('aria-valuenow', '720')
    fireEvent.keyDown(separator, { key: 'Home' })
    expect(separator).toHaveAttribute('aria-valuenow', '320')
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(events).toEqual([{ inspect: null }, 'closed'])
    unmount()
    expect(localStorage.getItem(key)).toBe('320')
    localStorage.removeItem(key)
  })

  it('collapses expansion before closing and balances dock reservations', () => {
    const baseline = Number(document.documentElement.style.getPropertyValue('--rightpanel-open')) || 0
    const closed: string[] = []
    const { unmount } = render(<SidePanel title="Inspector" onClose={() => closed.push('closed')}>Content</SidePanel>)
    const reservation = () => Number(document.documentElement.style.getPropertyValue('--rightpanel-open'))
    expect(reservation()).toBe(baseline + 1)
    fireEvent.click(screen.getByRole('button', { name: 'Expand to full width' }))
    expect(reservation()).toBe(baseline)
    expect(screen.getByRole('region', { name: 'Inspector' })).toBeTruthy()
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(closed).toEqual([])
    expect(screen.getByRole('button', { name: 'Expand to full width' })).toBeTruthy()
    expect(reservation()).toBe(baseline + 1)
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(closed).toEqual(['closed'])
    unmount()
    expect(reservation()).toBe(baseline)
  })

  it('delegates full-page expansion without switching its own mode', () => {
    const calls: string[] = []
    render(<SidePanel title="Dedicated" onClose={() => {}} onExpand={() => calls.push('navigate')}>Content</SidePanel>)
    fireEvent.click(screen.getByRole('button', { name: 'Open full page' }))
    expect(calls).toEqual(['navigate'])
    expect(screen.getByRole('separator')).toBeTruthy()
  })
})

describe('search and shortcut keyboard ownership', () => {
  it('returns focus to search after clear and lets empty Escape reach the modal', () => {
    const closed: string[] = []
    function Host() {
      const [value, setValue] = useState('query')
      return <Modal title="Search" onClose={() => closed.push('closed')}><SearchField value={value} onChange={setValue} /></Modal>
    }
    render(<Host />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear search' }))
    expect(document.activeElement).toBe(screen.getByRole('searchbox'))
    expect(screen.getByRole('searchbox')).toHaveValue('')
    fireEvent.keyDown(screen.getByRole('searchbox'), { key: 'Escape' })
    expect(closed).toEqual(['closed'])
  })

  it('records only a completed chord, cancels Escape and disarms on blur', () => {
    const recorded: string[] = []
    const bubbles: string[] = []
    render(<div onKeyDown={(event) => bubbles.push(event.key)}>
      <ShortcutRecorder label="Speak shortcut" value="Ctrl+Space" format={(value) => value}
        parse={(event) => event.ctrlKey && event.key === 'k' ? 'Ctrl+K' : ''} onRecord={(chord) => recorded.push(chord)} />
      <button>Outside</button>
    </div>)
    const recorder = screen.getByRole('button', { name: /activate to change/ })
    fireEvent.click(recorder)
    fireEvent.keyDown(recorder, { key: 'Control', ctrlKey: true })
    expect(recorder).toHaveAccessibleName(/Press the new/)
    fireEvent.keyDown(recorder, { key: 'k', ctrlKey: true })
    expect(recorded).toEqual(['Ctrl+K'])
    expect(bubbles).toEqual([])
    fireEvent.click(recorder)
    fireEvent.keyDown(recorder, { key: 'Escape' })
    expect(recorded).toEqual(['Ctrl+K'])
    expect(recorder).toHaveAccessibleName(/activate to change/)
    fireEvent.click(recorder)
    fireEvent.blur(recorder, { relatedTarget: screen.getByText('Outside') })
    fireEvent.keyDown(recorder, { key: 'k', ctrlKey: true })
    expect(recorded).toEqual(['Ctrl+K'])
    expect(bubbles).toEqual(['k'])
  })
})
