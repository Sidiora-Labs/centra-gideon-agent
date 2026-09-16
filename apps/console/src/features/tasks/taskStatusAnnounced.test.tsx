import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { TaskBoard } from './TaskBoard'
import { STATUSES, statusMeta } from './taskMeta'
import type { TaskItem } from '../../shared/data/api'


const task = (id: string, status: string, title: string): TaskItem =>
  ({ id, title, status, priority: 'medium' } as unknown as TaskItem)

const SRC = (rel: string) => readFileSync(join(process.cwd(), "src/features/tasks", rel), 'utf8')

describe('the status vocabulary has a label for every key', () => {
  it('all six backend statuses carry a non-empty label', () => {
    expect(STATUSES).toHaveLength(6)
    expect(STATUSES.map((s) => s.key).sort())
      .toEqual(['blocked', 'cancelled', 'done', 'in_progress', 'open', 'skipped'])
    for (const s of STATUSES) {
      expect(s.label.trim(), `${s.key} has a label`).not.toBe('')
      expect(s.label, `${s.key}'s label is prose, not the key`).not.toBe(s.key)
    }
  })
})

describe('the task list row announces its status', () => {
  const src = SRC('TasksListPage.tsx')

  it("the row's accessible name carries the status label, not just the title", () => {
    expect(src).toMatch(/<RowHitTarget label=\{`\$\{t\.title\} — \$\{sm\.label\}`\} \/>/)
  })

  it('the status glyph stays hidden, because the name now carries it', () => {
    expect(src, 'no aria-label was added to the glyph').not.toMatch(/<sm\.icon[^>]*aria-label/)
  })

  it('the row is not capped through rowSubject, which would truncate the status away', () => {
    expect(src, 'the list page does not route row names through the 55-char capper')
      .not.toMatch(/RowHitTarget label=\{rowSubject/)
  })
})

describe('the other three surfaces already said it, and still do', () => {
  it('the detail chip renders the label as text', () => {
    expect(SRC('TaskDetail.tsx')).toMatch(/<sm\.icon size=\{13\} \/> \{sm\.label\}/)
  })

  it('the card renders the label as a visible chip', () => {
    const src = SRC('TasksListPage.tsx')
    const card = src.slice(src.indexOf('function TaskCard'))
    expect(card).toMatch(/const badges = \[sm, pm, due\]/)
    expect(card).toMatch(/\{badge\.label\}/)
  })

  it("a board column names its group with the status, so its cards inherit it", () => {
    const { container } = render(
      <TaskBoard
        tasks={[task('a', 'cancelled', 'Cancel the duplicate weekly reminder'), task('b', 'done', 'Set up the template')]}
        onOpen={() => {}}
        onMove={vi.fn()}
      />,
    )
    const named = [...container.querySelectorAll('[role="group"][aria-label]')]
      .map((el) => el.getAttribute('aria-label') ?? '')
    expect(named.some((n) => n.startsWith(statusMeta('cancelled').label)), `saw: ${named.join(' | ')}`).toBe(true)
    expect(named.some((n) => n.startsWith(statusMeta('done').label)), `saw: ${named.join(' | ')}`).toBe(true)
  })
})
