import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const skills = [
  {
    key: 'auto/release', name: 'auto/release', description: 'Release steps',
    always: false, source: 'local', type: 'installed', provenance: 'auto' as const,
    loaded_by_agents: [],
  },
  {
    key: 'taught/notes', name: 'taught/notes', description: 'Note-taking steps',
    always: false, source: 'local', type: 'installed', provenance: 'taught' as const,
    loaded_by_agents: [],
  },
]

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      skills: () => Promise.resolve(skills),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      learningSummary: () => Promise.resolve({ window_days: 7, total: 0, new_skills: { count: 0, names: [] }, refined_skills: { count: 0, names: [] }, pending_proposals: { count: 0, names: [] }, facts: { count: 0, names: [] } }),
      skillFiles: () => Promise.resolve({ files: [] }),
    },
  }
})

describe('skill provenance', () => {
  it('shows the creation origin on the row and explains it in the inspector', async () => {
    const { SkillsPage } = await import('./SkillsPage')
    render(<SkillsPage query={{}} setQuery={() => {}} />)

    await waitFor(() => expect(screen.getByText('auto')).toBeInTheDocument())
    expect(screen.getByText('taught')).toBeInTheDocument()
    fireEvent.click(screen.getByText('auto/release'))
    expect(await screen.findByText('This skill was generated automatically.')).toBeInTheDocument()
  })
})
