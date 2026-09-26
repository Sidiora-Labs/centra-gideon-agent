import { fireEvent, render, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { GuardrailNotice } from './guardrail-notice'

const denial = {
  title: 'Action blocked',
  explanation: 'The requested export was denied.',
}

describe('GuardrailNotice recorded denial details', () => {
  it('preserves the donor policy, alternatives, and actual selection callback', () => {
    const onPick = vi.fn()
    const { container } = render(
      <GuardrailNotice {...denial} policy="POL-7" alternatives={['Request access', 'Use approved report']} onPick={onPick} />,
    )
    const notice = container.querySelector('[data-slot="guardrail-notice"]') as HTMLElement
    expect(within(notice).getByText('Action blocked')).toBeInTheDocument()
    expect(within(notice).getByText('The requested export was denied.')).toBeInTheDocument()
    expect(within(notice).getByText('POL-7')).toBeInTheDocument()
    expect(within(notice).getByText('try instead')).toBeInTheDocument()
    expect(within(notice).getAllByRole('button')).toHaveLength(2)
    fireEvent.click(within(notice).getByRole('button', { name: 'Use approved report' }))
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenCalledWith('Use approved report')
  })

  it('shows the denial alone when policy and alternatives are unknown', () => {
    const onPick = vi.fn()
    const { container, rerender } = render(<GuardrailNotice {...denial} onPick={onPick} />)
    const notice = container.querySelector('[data-slot="guardrail-notice"]') as HTMLElement
    expect(within(notice).getByText('Action blocked')).toBeInTheDocument()
    expect(within(notice).getByText('The requested export was denied.')).toBeInTheDocument()
    expect(notice.querySelector('.font-mono')).toBeNull()
    expect(within(notice).queryByText('try instead')).toBeNull()
    expect(within(notice).queryAllByRole('button')).toHaveLength(0)
    expect(onPick).not.toHaveBeenCalled()

    rerender(<GuardrailNotice {...denial} policy="POL-7" onPick={onPick} />)
    expect(within(notice).getByText('POL-7')).toBeInTheDocument()
    expect(within(notice).queryByText('try instead')).toBeNull()
    expect(within(notice).queryAllByRole('button')).toHaveLength(0)

    rerender(<GuardrailNotice {...denial} alternatives={['Request access']} onPick={onPick} />)
    expect(within(notice).queryByText('POL-7')).toBeNull()
    expect(within(notice).getByRole('button', { name: 'Request access' })).toBeInTheDocument()
  })

  it('hides blank policy and empty alternatives without inventing rows', () => {
    const { container, rerender } = render(<GuardrailNotice {...denial} policy="  " alternatives={[]} />)
    const notice = container.querySelector('[data-slot="guardrail-notice"]') as HTMLElement
    expect(notice.querySelector('.font-mono')).toBeNull()
    expect(within(notice).queryByText('try instead')).toBeNull()
    expect(within(notice).queryAllByRole('button')).toHaveLength(0)

    rerender(<GuardrailNotice {...denial} alternatives={['Request access']} />)
    expect(within(notice).getByText('Request access')).toBeInTheDocument()
    expect(within(notice).queryAllByRole('button')).toHaveLength(0)
  })
})
