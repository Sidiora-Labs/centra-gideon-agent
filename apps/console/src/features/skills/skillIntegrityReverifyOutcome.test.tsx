import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { resetDataStore } from '../../shared/data/data'
import type { SkillIntegrity, SkillItem } from '../../shared/data/api'

const verifySkill = vi.fn()

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      skillFiles: () => Promise.resolve({ files: [] }),
      verifySkill: (...args: unknown[]) => verifySkill(...args),
    },
  }
})

const skill: SkillItem = {
  key: 'release', name: 'release', description: 'Release steps', always: false,
  source: 'local', type: 'installed', loaded_by_agents: [], integrity: 'unverified',
}

const response = (integrity: SkillIntegrity['integrity']): SkillIntegrity => ({
  name: skill.name, integrity, ok: integrity === 'intact', unlocked: integrity === 'unverified',
  mutated: [], missing: [], added: [], summary: integrity,
})

beforeEach(() => {
  resetDataStore()
  sessionStorage.clear()
  verifySkill.mockReset()
})

describe('skill integrity re-verification', () => {
  it.each([
    ['intact', 'Integrity verified'],
    ['tampered', 'Changes found'],
    ['unverified', 'No integrity baseline'],
  ] as const)('renders the %s outcome beside Re-verify', async (integrity, outcome) => {
    verifySkill.mockResolvedValue(response(integrity))
    const { SkillInspector } = await import('./SkillInspector')
    render(<SkillInspector skill={skill} onDeleted={() => {}} />)

    const button = screen.getByRole('button', { name: /Re-verify/ })
    fireEvent.click(button)

    const status = await waitFor(() => screen.getByRole('status'))
    expect(status).toHaveTextContent(outcome)
    expect(status.parentElement).toContainElement(button)
    expect(verifySkill).toHaveBeenCalledWith(skill.name)
  })
})
