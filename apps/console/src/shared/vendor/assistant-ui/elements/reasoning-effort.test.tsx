import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ReasoningEffort, type EffortLevel } from './reasoning-effort'

const selected = (levels: readonly EffortLevel[], spent?: number | null, onSelect?: (key: string) => void) =>
  render(<ReasoningEffort levels={levels} selectedKey="high" spent={spent} onSelect={onSelect} />)

describe('ReasoningEffort measured budget contract', () => {
  it('accepts a localized heading without changing the selected effort', () => {
    const levels = [{ key: 'low', label: 'Low' }, { key: 'high', label: 'High' }]
    render(<ReasoningEffort levels={levels} selectedKey="high" heading="Pensando" />)
    expect(screen.getByText('Pensando')).toBeInTheDocument()
    expect(screen.queryByText('Thinking')).toBeNull()
    expect(screen.getByText('High')).toHaveAttribute('aria-current', 'true')
  })

  it('retains donor ratio, progress, selected state and action when measurements are known', () => {
    const onSelect = vi.fn()
    const levels = [{ key: 'low', label: 'Low', budget: 100 }, { key: 'high', label: 'High', budget: 200 }]
    selected(levels, 50, onSelect)

    expect(screen.getByText('50 / 200')).toBeInTheDocument()
    const progress = screen.getByRole('progressbar', { name: 'Thinking budget used' })
    expect(progress).toHaveAttribute('aria-valuenow', '25')
    expect(progress).toHaveAttribute('aria-valuetext', '50 of 200')
    expect(progress.querySelector('span')).toHaveStyle({ width: '25%' })
    expect(screen.getByRole('button', { name: 'High' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Low' }))
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('low')
  })

  it('moves between unknown and measured zero without inventing spent tokens', () => {
    const levels: EffortLevel[] = [{ key: 'low', label: 'Low' }, { key: 'high', label: 'High' }]
    const { rerender } = selected(levels)
    expect(screen.getByText('Thinking')).toBeInTheDocument()
    expect(screen.getByText('High')).toHaveAttribute('aria-current', 'true')
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByText(/\d+ \/ \d+/)).toBeNull()

    rerender(<ReasoningEffort levels={[levels[0]!, { ...levels[1]!, budget: 200 }]} selectedKey="high" spent={0} />)
    expect(screen.getByText('0 / 200')).toBeInTheDocument()
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')

    rerender(<ReasoningEffort levels={[levels[0]!, { ...levels[1]!, budget: 200 }]} selectedKey="high" />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByText('0 / 200')).toBeNull()

    rerender(<ReasoningEffort levels={[levels[0]!, { ...levels[1]!, budget: null }]} selectedKey="high" spent={25} />)
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByText('25 / 0')).toBeNull()
  })

  it.each([
    { budget: Number.NaN, spent: 4 },
    { budget: Number.POSITIVE_INFINITY, spent: 4 },
    { budget: 100, spent: Number.NaN },
    { budget: 100, spent: Number.NEGATIVE_INFINITY },
    { budget: 100, spent: null },
  ])('hides the ratio and progress for invalid measurement %#', ({ budget, spent }) => {
    const onSelect = vi.fn()
    selected([{ key: 'high', label: 'High', budget }], spent, onSelect)
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByText(/\/|NaN|Infinity/)).toBeNull()
    const button = screen.getByRole('button', { name: 'High' })
    expect(button).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(button)
    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith('high')
  })

  it('treats numeric zero budget as measured and keeps read-only levels inert', () => {
    const levels = [{ key: 'low', label: 'Low' }, { key: 'high', label: 'High', budget: 0 }]
    const { container, rerender } = selected(levels, 0)
    const root = container.querySelector('[data-slot="reasoning-effort"]') as HTMLElement
    expect(within(root).getByText('0 / 0')).toBeInTheDocument()
    const progress = within(root).getByRole('progressbar')
    expect(progress).toHaveAttribute('aria-valuenow', '0')
    expect(progress.querySelector('span')).toHaveStyle({ width: '0%' })
    expect(within(root).queryAllByRole('button')).toHaveLength(0)
    expect(within(root).getByText('High')).toHaveAttribute('aria-current', 'true')
    rerender(<ReasoningEffort levels={levels} selectedKey="high" spent={14} />)
    expect(within(root).getByText('14 / 0')).toBeInTheDocument()
    expect(within(root).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
  })
})
