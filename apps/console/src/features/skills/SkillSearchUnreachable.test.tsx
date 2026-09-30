import { describe, expect, it } from 'vitest'
import type { SkillCatalogueFailure, SkillSearchResult } from '../../shared/data/api'
import { skillSearchCatalogueState } from './skillLibraryState'

const FAILED_CATALOGUE: SkillCatalogueFailure[] = [
  {
    source: 'skills.sh',
    reason: "The request failed unexpectedly. Check the provider's logs and try again.",
  },
]

const REACHABLE_RESULT: SkillSearchResult = {
  id: 'postgres-helper',
  name: 'Postgres helper',
  description: 'Manage PostgreSQL deployments',
  source: 'skills.sh',
}

describe('skill catalogue search state', () => {
  it('distinguishes an unavailable source from a successful empty search', () => {
    expect(skillSearchCatalogueState([], 1, FAILED_CATALOGUE)).toBe('unavailable')
    expect(skillSearchCatalogueState([], 1, [])).toBe('empty')
  })

  it('retains the results state when another catalogue is unavailable', () => {
    expect(skillSearchCatalogueState([REACHABLE_RESULT], 2, FAILED_CATALOGUE)).toBe('unavailable')
    expect(REACHABLE_RESULT.id).toBe('postgres-helper')
  })

  it('distinguishes an unconfigured store from a query with no matches', () => {
    expect(skillSearchCatalogueState([], 0, [])).toBe('unconfigured')
    expect(skillSearchCatalogueState(null, null, [])).toBe('idle')
  })
})
