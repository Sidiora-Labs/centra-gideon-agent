import { describe, it, expect } from 'vitest'
import { modelRunProblem } from './modelRunProblem'
import type { AvailableModel, ModelProvider, ProviderHealth } from '../../shared/data/api'

describe('model chain serving status', () => {
  const model = { provider: 'Local', id: 'model', downloaded: false } as AvailableModel
  it('names an outage before proposing a missing download', () => {
    expect(modelRunProblem('Local:model', [model], [], [], { Local: 'connection refused' })).toContain('not answering')
    const provider = { name: 'Local', connection: { state: 'failed', rejected_credential: true } } as ModelProvider
    expect(modelRunProblem('Local:model', [model], [provider], [], {})).toContain('rejected its key')
  })
  it('keeps a half open provider failing and identifies actual missing weights', () => {
    const health = { name: 'Local', breaker_state: 'half_open' } as ProviderHealth
    expect(modelRunProblem('Local:model', [model], [], [health], {})).toContain('is failing')
    expect(modelRunProblem('Local:model', [model], [], [], {})).toContain('Not downloaded')
  })
})
