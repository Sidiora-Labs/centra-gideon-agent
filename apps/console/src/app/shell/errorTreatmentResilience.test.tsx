
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ErrorBoundary } from './ErrorBoundary'

const probe = vi.hoisted(() => ({ throws: 0 }))

vi.mock('../../shared/theme/personalities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../shared/theme/personalities')>()
  return {
    ...actual,
    resolvePersonality: () => {
      probe.throws += 1
      throw new Error('the personality registry exploded')
    },
  }
})

function Boom(): never {
  throw new Error('kaboom')
}

describe('a throwing personality lookup cannot take the app down', () => {
  it('renders the fallback, untreated, instead of propagating', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    expect(() =>
      render(
        <ErrorBoundary>
          <Boom />
        </ErrorBoundary>,
      ),
    ).not.toThrow()

    expect(probe.throws, 'the registry never threw — this test proves nothing').toBeGreaterThan(0)
    expect(screen.getByText('This page hit an error')).toBeTruthy()
    expect(screen.getByRole('button', { name: /retry/i })).toBeTruthy()
    const icon = screen.getByText('This page hit an error').parentElement?.querySelector('svg')
    expect(icon?.getAttribute('class')).toContain('text-on-surface-low')
    spy.mockRestore()
  })
})
