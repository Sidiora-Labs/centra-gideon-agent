import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { RegenerateMenu, type RegenerateOption } from './regenerate-menu'

const options: readonly RegenerateOption[] = [
  { id: 'fast', label: 'Fast model', detail: 'Low latency' },
  { id: 'deep', label: 'Deep model', detail: 'Higher reasoning' },
]

describe('RegenerateMenu labels on real model choices', () => {
  it('keeps the donor English options and current labels by default', async () => {
    const onOpenChange = vi.fn()
    const onPick = vi.fn()
    render(<RegenerateMenu options={options} open currentId="fast" onOpenChange={onOpenChange} onPick={onPick} />)

    const trigger = screen.getByRole('button', { name: 'Regenerate response options' })
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Fast model')).toBeInTheDocument()
    expect(screen.getByText('current')).toBeInTheDocument()
    expect(screen.getByText('Higher reasoning')).toBeInTheDocument()
    expect(screen.queryByText('Low latency')).toBeNull()

    await userEvent.click(screen.getByText('Deep model').closest('button')!)
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenCalledWith('deep')
    await userEvent.click(trigger)
    expect(onOpenChange).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('renders caller labels while keeping actual option names, details, and IDs', async () => {
    const onPick = vi.fn()
    const onOpenChange = vi.fn()
    render(<RegenerateMenu options={options} open currentId="deep"
      labels={{ options: 'Choose regeneration model', current: 'Selected model' }}
      onOpenChange={onOpenChange} onPick={onPick} />)

    const trigger = screen.getByRole('button', { name: 'Choose regeneration model' })
    expect(screen.queryByRole('button', { name: 'Regenerate response options' })).toBeNull()
    expect(screen.getByText('Selected model')).toBeInTheDocument()
    expect(screen.queryByText('current')).toBeNull()
    expect(screen.getByText('Low latency')).toBeInTheDocument()
    await userEvent.click(screen.getByText('Fast model').closest('button')!)
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick).toHaveBeenCalledWith('fast')
    await userEvent.click(trigger)
    expect(onOpenChange).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })

  it('falls back independently when only the options label is supplied', () => {
    render(<RegenerateMenu options={options} open currentId="fast" labels={{ options: 'Other model choices' }} onOpenChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'Other model choices' })).toBeInTheDocument()
    expect(screen.getByText('current')).toBeInTheDocument()
  })

  it('falls back independently when only the current label is supplied', () => {
    render(<RegenerateMenu options={options} open currentId="fast" labels={{ current: 'In use' }} onOpenChange={() => {}} />)
    expect(screen.getByRole('button', { name: 'Regenerate response options' })).toBeInTheDocument()
    expect(screen.getByText('In use')).toBeInTheDocument()
  })

  it('keeps the closed menu hidden and requests opening through the real trigger', async () => {
    const onOpenChange = vi.fn()
    render(<RegenerateMenu options={options} open={false} currentId="fast" labels={{ options: 'Choose regeneration model' }} onOpenChange={onOpenChange} />)
    const trigger = screen.getByRole('button', { name: 'Choose regeneration model' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Fast model')).toBeNull()
    await userEvent.click(trigger)
    expect(onOpenChange).toHaveBeenCalledTimes(1)
    expect(onOpenChange).toHaveBeenCalledWith(true)
  })

  it('keeps model choices informational when no picker callback is owned', () => {
    const { container } = render(<RegenerateMenu options={options} open currentId="fast" labels={{ current: 'In use' }} />)
    const menu = container.querySelector('[data-slot="regenerate-menu"]') as HTMLElement
    expect(within(menu).getByText('In use')).toBeInTheDocument()
    expect(within(menu).getByText('Higher reasoning')).toBeInTheDocument()
    expect(within(menu).queryByRole('button')).toBeNull()
  })
})
