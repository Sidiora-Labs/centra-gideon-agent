import { useState } from 'react'
import { fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { TaskItem } from '../../shared/data/api'
import { TodoList, type TodoItem } from '../../shared/vendor/assistant-ui/elements/todo-list'
import { TaskDetail } from './TaskDetail'

const plan: NonNullable<TaskItem['action_plan']> = [
  { content: 'Gather measurements', description: 'Old wording', completed: false, sequence: 1 },
  { description: 'Review evidence', completed: true, sequence: 2 },
]
const task: TaskItem = { id: 'task-7', title: 'Document the run', status: 'open', action_plan: plan }

function TaskView({ initial }: { initial: TaskItem }) {
  const [current, setCurrent] = useState(initial)
  const [editing, setEditing] = useState(false)
  const [deleted, setDeleted] = useState(false)
  return <>
    <TaskDetail task={current} onSaved={setCurrent} onDeleted={() => setDeleted(true)}
      editing={editing} onEditingChange={setEditing} />
    <output aria-label="Task state">{deleted ? 'deleted' : current.id}</output>
  </>
}

function ToggleView() {
  const [items, setItems] = useState<TodoItem[]>([
    { id: 'first', text: 'Gather measurements', status: 'pending' },
    { id: 'second', text: 'Review evidence', status: 'done' },
  ])
  const [last, setLast] = useState('')
  return <>
    <TodoList items={items} heading="Action plan" onToggle={(item, index) => {
      setLast(`${item.id}:${index}`)
      setItems(current => current.map((entry, position) => position === index
        ? { ...entry, status: entry.status === 'done' ? 'pending' : 'done' }
        : entry))
    }} />
    <output aria-label="Last selected step">{last}</output>
  </>
}

describe('TodoList read-only and interactive contracts', () => {
  it('preserves the donor read-only states, failure reason, revision, and default heading', () => {
    const items: TodoItem[] = [
      { id: 'p', text: 'Queued', status: 'pending' },
      { id: 'a', text: 'Working', status: 'active' },
      { id: 'd', text: 'Sent', status: 'done' },
      { id: 'f', text: 'Rejected', status: 'failed', reason: 'Policy refused the upload' },
    ]
    const { container } = render(<TodoList items={items} revision={4} />)
    const list = container.querySelector('[data-slot="todo-list"]') as HTMLElement
    expect(screen.getByText('Todos')).toBeTruthy()
    expect(screen.getByText('1/4 · rev 4')).toBeTruthy()
    expect(within(list).getAllByRole('listitem')).toHaveLength(4)
    expect(screen.getByText('Sent').className).toContain('line-through')
    expect(screen.getByText('Working').className).toContain('text-foreground/90')
    expect(screen.getByText('Rejected').className).toContain('text-red-600')
    expect(screen.getByText('Policy refused the upload')).toBeTruthy()
    expect(within(list).queryByRole('button')).toBeNull()
  })

  it('passes the real item and index to a keyboard-usable action, then updates completion', async () => {
    const user = userEvent.setup()
    render(<ToggleView />)
    expect(screen.getByText('Action plan')).toBeTruthy()
    expect(screen.getByText('1/2')).toBeTruthy()
    const complete = screen.getByRole('button', { name: 'Mark step done' })
    expect(complete.className).toContain('size-6')
    complete.focus()
    await user.keyboard(' ')
    expect(screen.getByLabelText('Last selected step').textContent).toBe('first:0')
    expect(screen.getByText('2/2')).toBeTruthy()
    expect(screen.getByText('Gather measurements').className).toContain('line-through')
    const incomplete = screen.getAllByRole('button', { name: 'Mark step incomplete' })[1]
    await user.click(incomplete)
    expect(screen.getByLabelText('Last selected step').textContent).toBe('second:1')
    expect(screen.getByText('1/2')).toBeTruthy()
    expect(screen.getByText('Review evidence').className).not.toContain('line-through')
  })

  it('allows an outer section to own the heading without changing the completion count', () => {
    render(<TodoList heading="" items={[{ id: 'x', text: 'Recorded result', status: 'done' }]} />)
    expect(screen.queryByText('Todos')).toBeNull()
    expect(screen.getByText('1/1')).toBeTruthy()
    expect(screen.getByText('Recorded result')).toBeTruthy()
  })
})

describe('TaskDetail action plan consumer', () => {
  it('maps real task step content, fallback description, order, and completion into TodoList', () => {
    const { container } = render(<TaskView initial={task} />)
    const heading = screen.getByRole('heading', { name: 'Action plan' })
    const section = heading.closest('section') as HTMLElement
    const list = section.querySelector('[data-slot="todo-list"]') as HTMLElement
    expect(list).toBeTruthy()
    expect(list.className).toContain('max-w-none')
    expect(within(list).getByText('1/2')).toBeTruthy()
    expect(within(list).getAllByRole('listitem').map(item => item.textContent)).toEqual([
      expect.stringContaining('Gather measurements'), expect.stringContaining('Review evidence'),
    ])
    expect(within(list).queryByText('Old wording')).toBeNull()
    expect(within(list).getByText('Review evidence').className).toContain('line-through')
    expect(within(list).getByRole('button', { name: 'Mark step done' })).toBeTruthy()
    expect(within(list).getByRole('button', { name: 'Mark step incomplete' })).toBeTruthy()
    expect(container.querySelectorAll('[data-slot="todo-list"]')).toHaveLength(1)
  })

  it('keeps project-managed tasks read-only while showing their actual progress', () => {
    render(<TaskView initial={{ ...task, provider: 'project' }} />)
    const section = screen.getByRole('heading', { name: 'Action plan' }).closest('section') as HTMLElement
    expect(within(section).getByText('1/2')).toBeTruthy()
    expect(within(section).getByText('Gather measurements')).toBeTruthy()
    expect(within(section).getByText('Review evidence')).toBeTruthy()
    expect(within(section).queryByRole('button')).toBeNull()
    expect(screen.getByText('Managed by project — read-only')).toBeTruthy()
  })

  it('routes a step action through the real task update and reports an unavailable gateway', async () => {
    render(<TaskView initial={task} />)
    fireEvent.click(screen.getByRole('button', { name: 'Mark step done' }))
    expect(screen.queryByRole('button', { name: 'Mark step done' })).toBeNull()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByLabelText('Task state').textContent).toBe('task-7')
    expect(screen.getByRole('button', { name: 'Mark step done' })).toBeTruthy()
  })

  it('does not invent content for a legacy step without either text field', () => {
    render(<TaskView initial={{ ...task, action_plan: [{ completed: false }] }} />)
    const section = screen.getByRole('heading', { name: 'Action plan' }).closest('section') as HTMLElement
    expect(within(section).getByText('0/1')).toBeTruthy()
    expect(within(section).getAllByRole('listitem')[0].textContent).toBe('pending')
  })

  it('does not create an action plan for a task without recorded steps', () => {
    const { container } = render(<TaskView initial={{ ...task, action_plan: [] }} />)
    expect(screen.queryByRole('heading', { name: 'Action plan' })).toBeNull()
    expect(container.querySelector('[data-slot="todo-list"]')).toBeNull()
  })
})
