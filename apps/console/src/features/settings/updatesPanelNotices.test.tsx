import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ThirdPartyNoticesLink } from './UpdatesPanel'

describe('customer-readable third-party notices entry', () => {
  it('opens the packaged notice route from the native Updates settings page', () => {
    render(<ThirdPartyNoticesLink />)

    const link = screen.getByRole('link', { name: 'Open third-party notices' })
    expect(link.getAttribute('href')).toBe('/THIRD_PARTY_NOTICES.txt')
    expect(link.getAttribute('target')).toBe('_blank')
  })
})
