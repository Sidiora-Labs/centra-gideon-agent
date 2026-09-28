import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { ProviderCard } from './ProviderCard'
import type { SettingsProvider } from '../../shared/data/api'

afterEach(() => cleanup())

describe('provider availability states', () => {
  it.each(['checking', 'available', 'unavailable', 'unknown'] as const)('renders the measured %s state', (state) => {
    const provider: SettingsProvider = {
      name: 'example-channel', displayName: 'Example Channel', enabled: true,
      provider: { type: 'channel' },
      availability: { state, reason: state === 'unavailable' ? 'Connect the account' : '', checkedAt: null },
    }
    const { getByText, getByRole } = render(
      <ProviderCard ext={provider} open={false} onOpenChange={() => {}} onChanged={() => {}} />,
    )
    expect(getByText(state)).toBeTruthy()
    expect(getByRole('button', { name: 'Check availability: Example Channel' })).toBeTruthy()
    if (state === 'unavailable') expect(getByText('Connect the account')).toBeTruthy()
  })
})
