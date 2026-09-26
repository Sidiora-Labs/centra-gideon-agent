import { describe, expect, it } from 'vitest'
import { Search } from 'lucide-react'
import { createElement, useState } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { CommandPalette } from './CommandPalette'
import type { Command } from './CommandPalette'
import { initialPalette, paletteReducer, searchCommands } from './paletteState'

const command = (id: string, label: string, keywords = ''): Command => ({ id, label, keywords, icon: Search, run: () => {} })

describe('command launcher state', () => {
  it('opens from an explicit search trigger, runs a real command, and resets for reopening', () => {
    let destination = ''
    function Launcher() {
      const [open, setOpen] = useState(false)
      return createElement('div', null,
        createElement('button', { onClick: () => setOpen(true) }, 'Search'),
        createElement(CommandPalette, { open, onOpenChange: setOpen, commands: [
          { id: 'files', label: 'Files', icon: Search, run: () => { destination = 'files' } },
          { id: 'projects', label: 'Projects', icon: Search, run: () => { destination = 'projects' } },
        ] }),
      )
    }
    render(createElement(Launcher))
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    const input = screen.getByLabelText('Search pages and actions')
    fireEvent.change(input, { target: { value: 'Files' } })
    fireEvent.click(screen.getByRole('option', { name: 'Files' }))
    expect(destination).toBe('files')
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    expect(screen.getByLabelText('Search pages and actions')).toHaveValue('')
    expect(screen.getByRole('option', { name: 'Projects' })).toBeInTheDocument()
    fireEvent.keyDown(window, { ctrlKey: true, key: 'k' })
    expect(screen.queryByRole('dialog')).toBeNull()
    fireEvent.keyDown(window, { ctrlKey: true, key: 'k' })
    expect(screen.getByLabelText('Search pages and actions')).toHaveValue('')
  })

  it('opens from the rail with a fresh query and closes with a fresh cursor', () => {
    const searched = paletteReducer(paletteReducer(initialPalette, { type: 'open' }), { type: 'search', value: 'files' })
    expect(searched).toEqual({ open: true, query: 'files', cursor: 0 })
    const moved = paletteReducer(searched, { type: 'move', delta: 1, count: 3 })
    expect(moved.cursor).toBe(1)
    expect(paletteReducer(moved, { type: 'close' })).toEqual(initialPalette)
    expect(paletteReducer(moved, { type: 'open' })).toEqual({ open: true, query: '', cursor: 0 })
  })

  it('keeps keyboard toggle, selection and bounds correct', () => {
    const opened = paletteReducer(initialPalette, { type: 'toggle' })
    expect(opened.open).toBe(true)
    expect(paletteReducer(opened, { type: 'select', index: 2 }).cursor).toBe(2)
    expect(paletteReducer(opened, { type: 'move', delta: 1, count: 0 }).cursor).toBe(0)
    expect(paletteReducer(opened, { type: 'move', delta: -1, count: 4 }).cursor).toBe(0)
    expect(paletteReducer(opened, { type: 'move', delta: 20, count: 4 }).cursor).toBe(3)
    expect(paletteReducer(opened, { type: 'toggle' })).toEqual(initialPalette)
  })

  it('ranks prefix, contained label and keyword hits while preserving source order', () => {
    const commands = [
      command('keyword', 'Build draft', 'calendar'),
      command('contains', 'Open calendar'),
      command('prefix1', 'Calendar'),
      command('prefix2', 'Calendar settings'),
      command('none', 'New conversation'),
    ]
    expect(searchCommands(commands, '')).toBe(commands)
    expect(searchCommands(commands, '  CALENDAR  ').map(item => item.id))
      .toEqual(['prefix1', 'prefix2', 'contains', 'keyword'])
    expect(searchCommands(commands, 'unknown')).toEqual([])
  })
})
