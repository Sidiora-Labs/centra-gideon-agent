import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const TASKS = join(process.cwd(), "src/features/tasks")
const read = (f: string) => readFileSync(join(TASKS, f), 'utf8')
const code = (f: string) => read(f).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the board is drag-only, and the census says so', () => {
  it('still moves cards by native drag', () => {
    const board = code('TaskBoard.tsx')
    expect(board, 'cards must still be a drag source').toMatch(/draggable=\{!readOnly\}/)
    expect(board, 'columns must still accept a drop').toMatch(/onDrop:/)
    expect(code('TasksListPage.tsx'), 'the drop must still persist a status').toMatch(/function moveTask\(id: string, status: string\)/)
  })

  it('offers no keyboard or click move ON the board — which is why the alternative matters', () => {
    const board = code('TaskBoard.tsx')
    const keyHandler = board.match(/onKeyDown=\{\(e\) => \{[^}]*\}[^}]*\}/)?.[0] ?? ''
    expect(keyHandler, 'the card keydown must still exist').toMatch(/Enter/)
    expect(keyHandler, 'and it must still only OPEN — no move keys').not.toMatch(/onMove|ArrowLeft|ArrowRight/)
  })
})

describe('the non-drag path that makes the board legal', () => {
  it('the edit form exposes Status as a real, keyboard-operable control', () => {
    const form = code('TaskForm.tsx')
    expect(form, 'a labelled Status field must exist').toMatch(/<Field label="Status">/)
    expect(form, 'and it must be the Segmented control, fed from the canonical registry')
      .toMatch(/<Field label="Status">\s*<Segmented[\s\S]{0,120}?STATUSES\.map/)
    expect(code('taskMeta.tsx')).toMatch(/export const STATUSES: StatusMeta\[\]/)
  })

  it('the bulk bar can set a status without any drag', () => {
    expect(code('TasksListPage.tsx'), 'the second non-drag path')
      .toMatch(/runBulk\('update', \{ status:/)
  })

  it('a card is still openable from the keyboard (the fix that got it here)', () => {
    const board = code('TaskBoard.tsx')
    expect(board).toMatch(/role="button" tabIndex=\{0\} aria-label=\{t\.title\}/)
    expect(board, 'Space must not scroll the column instead of opening').toMatch(/e\.key === ' '[\s\S]{0,60}?preventDefault\(\)/)
  })
})
