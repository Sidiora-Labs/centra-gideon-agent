import { fireEvent, render, screen } from '@testing-library/react'
import { Blocks } from 'lucide-react'
import { describe, expect, it, vi } from 'vitest'
import { BentoCard } from './bento'

describe('settings widget read failures', () => {
  it('owns the operation, server reason, and retry in the card', () => {
    const refresh = vi.fn()
    render(<BentoCard icon={Blocks} title="Workflows" onClick={vi.fn()} status="error"
      error={new Error('workflow registry unavailable')} refresh={refresh} operation="workflows">
      <div>12 workflows installed</div>
    </BentoCard>)

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('Couldn’t load workflows')
    expect(alert.textContent).toContain('workflow registry unavailable')
    expect(screen.queryByText('12 workflows installed')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(refresh).toHaveBeenCalledOnce()
  })

  it('uses server wording when the rejection is opaque', () => {
    render(<BentoCard icon={Blocks} title="Autonomous loops" onClick={vi.fn()} status="error"
      error={new TypeError('Failed to fetch')} refresh={vi.fn()} operation="autonomous loops" />)
    expect(screen.getByRole('alert').textContent).toContain("Couldn’t load autonomous loops: The server didn't respond.")
  })
})
