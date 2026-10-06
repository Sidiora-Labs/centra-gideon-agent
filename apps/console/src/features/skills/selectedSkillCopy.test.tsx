import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SkillInspector } from './SkillInspector'
import { api, type SkillItem } from '../../shared/data/api'

const fixtures = vi.hoisted(() => ({ document: {
  content: 'Owner source', revision: 'document-revision', recognized_copies: true,
  refinements: [{ id: 'stable-refinement', version: 2, text: 'Accepted addition' }],
} }))
vi.mock('../../shared/data/data', () => ({
  useQuery: (key: string) => ({ data: key.startsWith('skill:document:') ? fixtures.document : [], refresh: vi.fn() }),
  invalidateKeys: vi.fn(),
  useMutation: vi.fn(),
}))
vi.mock('../../shared/data/api', () => ({
  api: { updateSkill: vi.fn(async () => ({ ok: true })), verifySkill: vi.fn(),
    revertSkillRefinement: vi.fn(async () => ({ ok: true, reverted: 1 })),
    skillBundledChoice: vi.fn(async () => ({ ok: true })) }, ApiError: class extends Error {},
}))
const skill: SkillItem = { key: 'chosen-copy', copy: 'chosen-copy', name: 'namespace/sample',
  description: 'Skill description', always: false, source: 'agent-local', type: 'installed',
  agent: 'researcher', loaded_by_agents: ['researcher'], integrity: 'edited', bundled_update: 'offered-digest' }

beforeEach(() => vi.clearAllMocks())
describe('selected skill copy', () => {
  it('edits authored source with the selected copy and revision', async () => {
    render(<SkillInspector skill={skill} onDeleted={vi.fn()} />)
    expect(screen.getByText('Edited — files differ from their install baseline')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Edit SKILL.md' }))
    expect(screen.getByRole('textbox')).toHaveValue('Owner source')
    expect(screen.getByRole('status')).toHaveTextContent('Unchanged copies')
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Updated owner source' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.updateSkill).toHaveBeenCalledWith('namespace/sample', 'Updated owner source', 'document-revision', 'chosen-copy'))
  })
  it('reverts a stable refinement and keeps the offered version for this copy', async () => {
    render(<SkillInspector skill={skill} onDeleted={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Revert this refinement' }))
    await waitFor(() => expect(api.revertSkillRefinement).toHaveBeenCalledWith('namespace/sample', 'chosen-copy', 'stable-refinement'))
    fireEvent.click(screen.getByRole('button', { name: 'Keep my version' }))
    await waitFor(() => expect(api.skillBundledChoice).toHaveBeenCalledWith('namespace/sample', 'chosen-copy', 'offered-digest', 'keep'))
  })
  it('shared copies present refinements without mutation controls', () => {
    render(<SkillInspector skill={{ ...skill, source: 'shared', read_only: true }} onDeleted={vi.fn()} />)
    expect(screen.getByText('Accepted addition')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Edit SKILL.md' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Revert this refinement' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use shipped version' })).not.toBeInTheDocument()
  })
})
