import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ContextMenu } from './ContextMenu'


const ITEMS = () => [
  { label: 'Open', onSelect: vi.fn() },
  { label: 'Complete', onSelect: vi.fn() },
  { label: 'Delete', onSelect: vi.fn(), danger: true },
]

function mount(items = ITEMS()) {
  const view = render(
    <div>
      <button type="button">row hit target</button>
      <ContextMenu items={items}><div>row body</div></ContextMenu>
    </div>,
  )
  const invoker = screen.getByRole('button', { name: 'row hit target' })
  invoker.focus()
  return { ...view, invoker, body: screen.getByText('row body') }
}

const rows = () => screen.getAllByRole('menuitem')

describe('the row context menu opens from the keyboard', () => {
  it('Shift+F10 opens it', () => {
    const { body } = mount()
    expect(screen.queryByRole('menu')).toBeNull()
    act(() => { fireEvent.keyDown(body, { key: 'F10', shiftKey: true }) })
    expect(screen.getByRole('menu')).toBeTruthy()
    expect(rows().map((r) => r.textContent?.trim())).toEqual(['Open', 'Complete', 'Delete'])
  })

  it('the dedicated ContextMenu key opens it', () => {
    const { body } = mount()
    act(() => { fireEvent.keyDown(body, { key: 'ContextMenu' }) })
    expect(screen.getByRole('menu')).toBeTruthy()
  })

  it('a bare F10 does not', () => {
    const { body } = mount()
    act(() => { fireEvent.keyDown(body, { key: 'F10' }) })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('focus moves into the menu, onto the first row', () => {
    const { body } = mount()
    act(() => { fireEvent.keyDown(body, { key: 'F10', shiftKey: true }) })
    expect(document.activeElement).toBe(rows()[0])
  })

  it('Escape hands focus back to where it started', () => {
    const { body, invoker } = mount()
    act(() => { fireEvent.keyDown(body, { key: 'F10', shiftKey: true }) })
    expect(document.activeElement).not.toBe(invoker)
    act(() => { fireEvent.keyDown(document, { key: 'Escape' }) })
    expect(document.activeElement).toBe(invoker)
  })

  it('a disabled menu stays closed', () => {
    render(<ContextMenu items={ITEMS()} disabled><div>quiet row</div></ContextMenu>)
    act(() => { fireEvent.keyDown(screen.getByText('quiet row'), { key: 'F10', shiftKey: true }) })
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('the right-click route is untouched', () => {
    const { body } = mount()
    act(() => { fireEvent.contextMenu(body) })
    expect(screen.getByRole('menu')).toBeTruthy()
  })
})

describe('the population this reaches', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  it('14 surfaces use the primitive, so the fix is not a per-page patch', () => {
    const files = walk(join(SRC, "features")).filter((abs) => /<ContextMenu[\s>]/.test(readFileSync(abs, 'utf8')))
    expect(files.length, `consumers:\n${files.map((f) => f.slice(SRC.length + 1)).join('\n')}`).toBeGreaterThanOrEqual(14)
  })

  it('the primitive still binds a keyboard opener', () => {
    const code = readFileSync(join(SRC, 'shared/ui/motion/ContextMenu.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).toMatch(/onKeyDown=\{keyboardOpen\}/)
    expect(code).toMatch(/event\.key !== 'ContextMenu' && !\(event\.key === 'F10' && event\.shiftKey\)/)
  })
})
