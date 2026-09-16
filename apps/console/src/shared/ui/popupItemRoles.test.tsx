import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { MenuRow } from './Popover'


describe('MenuRow carries the item role its container promises', () => {
  it('is a plain button by default — right for the 28 role-less popovers', () => {
    render(<MenuRow label="Rename" onClick={vi.fn()} />)
    const b = screen.getByRole('button', { name: 'Rename' })
    expect(b.getAttribute('role')).toBeNull()
    expect(b.getAttribute('aria-selected')).toBeNull()
    expect(b.getAttribute('aria-checked')).toBeNull()
  })

  it('is an option, with the selected state, inside a listbox', () => {
    render(<MenuRow role="option" label="Cards view" selected onClick={vi.fn()} />)
    expect(screen.getByRole('option', { name: 'Cards view' }).getAttribute('aria-selected')).toBe('true')
  })

  it('reports an unselected option as false, not as absent', () => {
    render(<MenuRow role="option" label="Kanban board" onClick={vi.fn()} />)
    expect(screen.getByRole('option', { name: 'Kanban board' }).getAttribute('aria-selected')).toBe('false')
  })

  it('is a menuitem with NO state for an action row', () => {
    render(<MenuRow role="menuitem" label="Peek" selected onClick={vi.fn()} />)
    const b = screen.getByRole('menuitem', { name: 'Peek' })
    expect(b.getAttribute('aria-checked')).toBeNull()
    expect(b.getAttribute('aria-selected')).toBeNull()
  })

  it('uses aria-checked for a radio item', () => {
    render(<MenuRow role="menuitemradio" label="Agent" selected onClick={vi.fn()} />)
    expect(screen.getByRole('menuitemradio', { name: 'Agent' }).getAttribute('aria-checked')).toBe('true')
  })
})

describe('every popup container in the tree contains its item type', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  const ITEM_OF: Record<string, RegExp> = {
    menu: /role="(menuitem|menuitemradio|menuitemcheckbox)"|<MenuRow[\s\S]{0,400}?role="(menuitem|menuitemradio)"/,
    listbox: /role="option"|<MenuRow[\s\S]{0,400}?role="option"/,
  }

  const containers = walk(SRC).flatMap((f) => {
    const src = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    return [...src.matchAll(/role="(menu|listbox)"/g)].map((m) => ({
      file: f.slice(SRC.length + 1),
      role: m[1],
      satisfied: ITEM_OF[m[1]].test(src),
    }))
  })

  it('finds them all (not vacuously green)', () => {
    expect(containers.length, 'the matcher must find the popup containers').toBeGreaterThanOrEqual(6)
    expect(containers.map((c) => c.file)).toContain('shared/ui/Segmented.tsx')
    expect(containers.map((c) => c.file)).toContain('shared/ui/motion/ContextMenu.tsx')
  })

  it('has no container that declares a role without the matching items', () => {
    const lying = containers.filter((c) => !c.satisfied).map((c) => `${c.file} (role=${c.role})`)
    expect(lying, 'a menu with no menuitems / a listbox with no options announces an empty container').toEqual([])
  })

  it('a component with an arrow-key cursor over a list declares a container role', () => {
    const cursored = walk(SRC).flatMap((f) => {
      const src = readFileSync(f, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const hasCursor = /key === 'ArrowDown'|key === 'ArrowUp'|useMenuCursor\(|menuCursorKeydown\(/.test(src)
      const rendersRows = /(?:options|filtered|opts|items|rows)\s*\.map\(/.test(src) && /<(?:motion\.)?button/.test(src)
      if (!hasCursor || !rendersRows) return []
      return [{ file: f.slice(SRC.length + 1), declares: /role="(?:listbox|menu|combobox|grid|tree)"/.test(src) }]
    })

    expect(cursored.length, 'the shape sweep must find the cursored lists').toBeGreaterThanOrEqual(4)
    expect(cursored.map((c) => c.file), 'the one this check was written for').toContain('shared/ui/Combobox.tsx')

    const silent = cursored.filter((c) => !c.declares).map((c) => c.file)
    expect(silent, `these drive a cursor over rows and tell assistive tech nothing:\n${silent.join('\n')}`)
      .toEqual([])
  })

  it("Segmented's trigger advertises the popup, like ProjectPicker's does", () => {
    const seg = readFileSync(join(SRC, 'shared/ui/Segmented.tsx'), 'utf8')
    expect(seg).toMatch(/aria-haspopup="listbox"/)
    expect(seg).toMatch(/aria-expanded=\{open\}/)
  })
})
