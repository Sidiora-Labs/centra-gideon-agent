import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const detail = readFileSync(join(process.cwd(), 'src/features/tasks/TaskDetail.tsx'), 'utf8')
const todo = readFileSync(join(process.cwd(), 'src/shared/vendor/assistant-ui/elements/todo-list.tsx'), 'utf8')

function buttonFor(source: string, label: string): string {
  const tag = source.match(/<button[\s\S]*?className="[^"]*"/g)?.find(candidate => candidate.includes(label))
  if (tag) return tag
  throw new Error(`No button found for ${label}`)
}

describe('task panel tick targets clear the 24px floor', () => {
  it('keeps both criterion and donor TodoList actions on a 24px target', () => {
    expect(buttonFor(detail, 'Mark criterion')).toMatch(/\bsize-6\b/)
    expect(buttonFor(todo, 'Mark step')).toMatch(/\bsize-6\b/)
  })

  it('reclaims space horizontally without overlapping stacked actions', () => {
    const criterion = buttonFor(detail, 'Mark criterion')
    const step = buttonFor(todo, 'Mark step')
    expect(criterion).toMatch(/-mx-1\b/)
    expect(step).toMatch(/-mx-0\.5\b/)
    for (const tag of [criterion, step]) expect(tag).not.toMatch(/-my-|-mt-|-mb-/)
  })

  it('keeps the visual marker smaller than its target and gives keyboard focus a ring', () => {
    expect(detail.slice(detail.indexOf('Mark criterion'), detail.indexOf('Mark criterion') + 700))
      .toMatch(/<span className="inline-flex size-4 [^"]*rounded-sm/)
    expect(todo).toMatch(/<span aria-hidden className="flex size-4 h-5 items-center justify-center">\{marker\}<\/span>/)
    expect(buttonFor(todo, 'Mark step')).toContain('focus-visible:ring-2')
  })
})
