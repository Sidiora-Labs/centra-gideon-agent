import { describe, expect, it, vi } from 'vitest'
import { render } from '@testing-library/react'
import { TaskBoard } from './TaskBoard'
import type { TaskItem } from '../../shared/data/api'


const task = (id: string, status: string): TaskItem => ({
  id, title: `Task ${id}`, status, priority: 'medium',
} as unknown as TaskItem)

describe('kanban column scroll regions', () => {
  it('every column is a keyboard-reachable, named region', () => {
    const { container } = render(
      <TaskBoard tasks={[task('a', 'open'), task('b', 'in_progress')]} onOpen={() => {}} onMove={vi.fn()} />,
    )
    const scrollers = [...container.querySelectorAll('.overflow-y-auto')]
    expect(scrollers.length).toBeGreaterThan(0)
    for (const el of scrollers) {
      expect(el.getAttribute('tabindex'), 'column scroll region must own a tab stop').toBe('0')
      expect(el.getAttribute('role')).toBe('group')
      expect(el.getAttribute('aria-label')).toBeTruthy()
    }
  })

  it('the region name carries the column label and a correctly pluralised count', () => {
    const { container } = render(
      <TaskBoard tasks={[task('a', 'open')]} onOpen={() => {}} onMove={vi.fn()} />,
    )
    const labels = [...container.querySelectorAll('.overflow-y-auto')]
      .map((el) => el.getAttribute('aria-label') ?? '')
    expect(labels.some((l) => /— 1 task$/.test(l)), `got: ${labels.join(' | ')}`).toBe(true)
    expect(labels.every((l) => !/— 1 tasks$/.test(l)), `got: ${labels.join(' | ')}`).toBe(true)
    expect(labels.every((l) => /^\S/.test(l)), `got: ${labels.join(' | ')}`).toBe(true)
  })
})
