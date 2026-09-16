import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ProjectKnowledgeList, SHARING_POLICY_LABEL } from './ProjectsSection'
import type { ProjectKnowledgeItem, SharingPolicy } from '../../shared/data/api'


function item(over: Partial<ProjectKnowledgeItem> = {}): ProjectKnowledgeItem {
  return {
    id: 'k1', title: 'Cold start latency', kind: 'fact', summary: '', updated_at: '2026-08-11T00:00:00Z',
    project_id: 'p-alpha', run_id: 'r-1', sharing_policy: 'private', source_project: '', ...over,
  }
}

describe('ProjectKnowledgeList', () => {
  it('labels every sharing policy the wire type allows', () => {
    const policies = Object.keys(SHARING_POLICY_LABEL) as SharingPolicy[]
    expect(policies.sort()).toEqual(['private', 'shared'])
    render(<ProjectKnowledgeList items={policies.map((p, i) => item({ id: `k${i}`, title: `Item ${p}`, sharing_policy: p }))} />)
    for (const p of policies) expect(screen.getByText(SHARING_POLICY_LABEL[p])).toBeTruthy()
  })

  it('names the owning project on a shared cross-container item', () => {
    render(<ProjectKnowledgeList items={[
      item({ id: 'own', title: 'Mine' }),
      item({ id: 'foreign', title: 'Theirs', project_id: 'p-beta', sharing_policy: 'shared', source_project: 'Beta' }),
    ]} />)
    expect(screen.getByText('Theirs')).toBeTruthy()
    expect(screen.getByText('Beta')).toBeTruthy()
    expect(screen.getByTitle('Shared from Beta')).toBeTruthy()
    expect(screen.queryByTitle('Shared from Mine')).toBeNull()
  })

  it('a policy value the wire invents renders humanized, never as "unmapped:" jargon', () => {
    render(<ProjectKnowledgeList items={[
      item({ id: 'drifted', title: 'From the future', sharing_policy: 'org_wide' as SharingPolicy }),
    ]} />)
    expect(screen.getByText('Org wide')).toBeTruthy()
    expect(screen.queryByText(/unmapped/)).toBeNull()
  })
})
