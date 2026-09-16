import { useState } from 'react'
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ActionPlanItem, ExitCriterion, TaskItem, TaskNote } from '../../shared/data/api'
import { ChecklistEditor, DependencyEditor, NotesEditor } from './formControls'
import { TaskRequestLease, chooseTaskProject, dependencyCandidates, draftToPayload, emptyDraft, taskChecklistPatch, toDraft, useTaskOperation } from './taskEditorState'

const task = (id: string, title = id, dependencies: string[] = []): TaskItem => ({ id, title, status: 'open', dependencies: dependencies.map(depends_on_task_id => ({ depends_on_task_id, dependency_type: 'BLOCKS' })) })

describe('task draft and selection rules', () => {
  it('creates fresh collection defaults and serializes only editable fields', () => {
    const first = emptyDraft()
    first.labels!.push('first')
    expect(emptyDraft().labels).toEqual([])
    const payload = draftToPayload({ ...first, id: 'server-id', title: '  Review  ', provider: 'native', depends_on: ['before'], project_id: 'project' })
    expect(payload).toMatchObject({ title: 'Review', status: 'open', priority: 'medium', project_id: 'project', dependencies: [{ depends_on_task_id: 'before', dependency_type: 'BLOCKS' }] })
    expect(payload).not.toHaveProperty('id')
    expect(payload).not.toHaveProperty('provider')
    expect(payload).not.toHaveProperty('depends_on')
  })
  it('keeps explicit task-list ownership authoritative over project fallback', () => {
    expect(draftToPayload({ ...emptyDraft(), title: 'A', project_id: 'p', task_list_id: 'list' })).not.toHaveProperty('project_id')
    expect(draftToPayload({ ...emptyDraft(), title: 'A', project_id: 'p' })).toHaveProperty('project_id', 'p')
  })
  it('normalizes missing arrays and only edits BLOCKS dependencies', () => {
    const record = task('self', 'Title', ['prerequisite'])
    record.dependencies!.push({ depends_on_task_id: 'reference', dependency_type: 'REQUIRED_FOR' })
    const draft = toDraft(record)
    expect(draft.depends_on).toEqual(['prerequisite'])
    for (const key of ['notes', 'research_notes', 'execution_notes', 'action_plan', 'exit_criteria', 'labels'] as const) expect(draft[key]).toEqual([])
    expect(record).not.toHaveProperty('notes')
  })
  it('resolves list ownership before active, builtin, and first project defaults', () => {
    const projects = [{ id: 'first', name: 'First' }, { id: 'builtin', name: 'Default', is_builtin: true }, { id: 'active', name: 'Active' }]
    const lists = [{ id: 'list', name: 'Work', project_id: 'first' }]
    expect(chooseTaskProject(projects, lists, 'list', 'active')).toBe('first')
    expect(chooseTaskProject(projects, lists, '', 'active')).toBe('active')
    expect(chooseTaskProject(projects, lists, '', 'removed')).toBe('builtin')
    expect(chooseTaskProject(projects.slice(0, 1), lists, '', null)).toBe('first')
    expect(chooseTaskProject([], [], '', null)).toBe('')
  })
  it('excludes selected/self tasks, detects transitive cycles and caps search in source order', () => {
    const graph = [task('self'), task('child', 'Child', ['self']), task('descendant', 'Descendant', ['child']), task('safe', 'Safe')]
    expect(dependencyCandidates(graph, 'self', ['safe'], '').map(({ task, cyclic }) => [task.id, cyclic])).toEqual([['child', true], ['descendant', true]])
    expect(dependencyCandidates(graph, 'self', [], ' SAFE ')).toEqual([{ task: graph[3], cyclic: false }])
    const many = Array.from({ length: 55 }, (_, index) => task(String(index)))
    expect(dependencyCandidates(many, undefined, [], '')).toHaveLength(40)
    expect(dependencyCandidates(many, undefined, [], '')[39].task.id).toBe('39')
  })
  it('toggles legacy exit states consistently without replacing untouched criteria or plan details', () => {
    const record = { ...task('work'), exit_criteria: [{ description: 'Legacy', status: 'complete' }, { description: 'Pending', met: false }], action_plan: [{ description: 'Step', content: 'Content', completed: false, sequence: 3 }] } satisfies TaskItem
    const exit = taskChecklistPatch(record, 'exit', 0).exit_criteria as ExitCriterion[]
    expect(exit[0]).toEqual({ description: 'Legacy', status: 'incomplete', met: false })
    expect(exit[1]).toBe(record.exit_criteria[1])
    expect((taskChecklistPatch(record, 'step', 0).action_plan as ActionPlanItem[])[0]).toMatchObject({ completed: true, content: 'Content', sequence: 3 })
    expect(record.action_plan[0].completed).toBe(false)
  })
})

describe('task request ownership', () => {
  it('allows one pending operation and cannot release a newer task request with a stale ticket', () => {
    const lease = new TaskRequestLease()
    const old = lease.begin()!
    expect(lease.begin()).toBeNull()
    lease.reset()
    const current = lease.begin()!
    expect(lease.finish(old)).toBe(false)
    expect(lease.owns(current)).toBe(true)
    expect(lease.finish(current)).toBe(true)
    expect(lease.begin()).not.toBeNull()
  })
  it('suppresses a completed operation after the selected task changes', async () => {
    let finish!: (value: string) => void
    const completion = new Promise<string>(resolve => { finish = resolve })
    const saved: string[] = []
    const { result, rerender } = renderHook(({ id }) => useTaskOperation(id), { initialProps: { id: 'first' } })
    let pending!: Promise<void>
    act(() => { pending = result.current.run(() => completion, value => saved.push(value), 'Failed') })
    expect(result.current.busy).toBe(true)
    rerender({ id: 'second' })
    await act(async () => { finish('first'); await pending })
    expect(saved).toEqual([])
    expect(result.current.busy).toBe(false)
    await act(async () => { await result.current.run(async () => 'second', value => saved.push(value), 'Failed') })
    expect(saved).toEqual(['second'])
  })
  it('retains real operation errors and clears them on the next successful write', async () => {
    const { result } = renderHook(() => useTaskOperation('task'))
    await act(async () => { await result.current.run(async () => { throw new Error('Rejected') }, () => {}, 'Failed') })
    expect(result.current.error).toBe('Rejected')
    await act(async () => { await result.current.run(async () => 1, () => {}, 'Failed') })
    expect(result.current.error).toBe('')
    expect(result.current.busy).toBe(false)
  })
})

function ChecklistState() {
  const [items, setItems] = useState<{ description: string; completed: boolean }[]>([])
  return <><ChecklistEditor items={items} onChange={setItems} doneKey="completed" placeholder="Add a step" /><output aria-label="Checklist state">{JSON.stringify(items)}</output></>
}
function NotesState() {
  const [items, setItems] = useState<TaskNote[]>([{ content: 'Existing', created_at: '2026-09-01' }])
  return <><NotesEditor items={items} onChange={setItems} placeholder="Add a note" /><output aria-label="Notes state">{JSON.stringify(items)}</output></>
}

describe('task editor DOM interactions', () => {
  it('commits a blurred checklist entry only once, then toggles and confirms its deletion', () => {
    render(<ChecklistState />)
    const input = screen.getByRole('textbox', { name: 'Add a step' })
    fireEvent.change(input, { target: { value: '  Write release notes  ' } })
    fireEvent.blur(input)
    fireEvent.click(screen.getByRole('button', { name: 'Add' }))
    expect(screen.getByLabelText('Checklist state').textContent).toBe('[{"description":"Write release notes","completed":false}]')
    fireEvent.click(screen.getByRole('button', { name: 'Mark done: Write release notes' }))
    expect(screen.getByRole('button', { name: 'Mark not done: Write release notes' })).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Remove'))
    expect(screen.getByLabelText('Checklist state').textContent).toContain('Write release notes')
    fireEvent.click(screen.getByRole('button', { name: 'Remove?' }))
    expect(screen.getByLabelText('Checklist state').textContent).toBe('[]')
  })
  it('does not consume a composing Enter, while ordinary Enter commits the entry', () => {
    render(<ChecklistState />)
    const input = screen.getByRole('textbox', { name: 'Add a step' })
    fireEvent.change(input, { target: { value: 'Composition' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(screen.getByLabelText('Checklist state').textContent).toBe('[]')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(screen.getByLabelText('Checklist state').textContent).toContain('Composition')
  })
  it('appends notes once and preserves timestamps of existing notes', () => {
    render(<NotesState />)
    const input = screen.getByRole('textbox', { name: 'Add a note' })
    fireEvent.change(input, { target: { value: ' Second ' } })
    fireEvent.blur(input)
    fireEvent.click(screen.getByLabelText('Add note'))
    expect(JSON.parse(screen.getByLabelText('Notes state').textContent!)).toEqual([{ content: 'Existing', created_at: '2026-09-01' }, { content: 'Second' }])
    fireEvent.click(screen.getAllByLabelText('Remove note')[1])
    expect(JSON.parse(screen.getByLabelText('Notes state').textContent!)).toEqual([{ content: 'Existing', created_at: '2026-09-01' }])
  })
  it('leaves cyclic prerequisite choices focusable but inert and allows valid selections', async () => {
    function Selection() {
      const [selected, setSelected] = useState<string[]>([])
      return <><DependencyEditor selfId="self" allTasks={[task('self'), task('cycle', 'Cyclic', ['self']), task('safe', 'Safe')]} value={selected} onChange={setSelected} /><output aria-label="Selected prerequisites">{selected.join(',')}</output></>
    }
    render(<Selection />)
    fireEvent.click(screen.getByRole('button', { name: 'Add prerequisite' }))
    const cyclic = await screen.findByRole('button', { name: /Cyclic/ })
    expect(cyclic).toHaveAttribute('aria-disabled', 'true')
    expect(cyclic).not.toBeDisabled()
    cyclic.focus()
    expect(document.activeElement).toBe(cyclic)
    fireEvent.click(cyclic)
    expect(screen.getByLabelText('Selected prerequisites').textContent).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'Safe' }))
    await waitFor(() => expect(screen.getByLabelText('Selected prerequisites').textContent).toBe('safe'))
    fireEvent.click(screen.getByLabelText('Remove prerequisite'))
    expect(screen.getByLabelText('Selected prerequisites').textContent).toBe('')
  })
})
