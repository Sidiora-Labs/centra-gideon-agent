import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { InstanceCard } from './ModelBackends'
import type { ModelProvider } from '../../shared/data/api'

afterEach(() => cleanup())

describe('model provider instance readiness', () => {
  it.each(['checking', 'connected', 'failed', 'untestable'] as const)('renders the measured %s connection', (state) => {
    const provider: ModelProvider = {
      name: 'example-instance', type: 'remote', capabilities: ['chat'], credential_status: 'ok',
      connection: { state, detail: state === 'failed' ? 'Endpoint refused the connection' : '', rejected_credential: false, checked_at: null },
    }
    const { getByText } = render(<InstanceCard provider={provider} models={[]} onChanged={() => {}} />)
    expect(getByText(state)).toBeTruthy()
  })
})
