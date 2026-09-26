import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ToolGroup } from './tool-group.aui'

function mount(count: number, options: { countLabel?: (count: number) => string; active?: boolean } = {}) {
  return render(
    <ToolGroup.Root>
      <ToolGroup.Trigger count={count} countLabel={options.countLabel} active={options.active} />
      <ToolGroup.Content>Recorded tool output</ToolGroup.Content>
    </ToolGroup.Root>,
  )
}

describe('ToolGroup count labels on the mounted trigger', () => {
  it.each([
    [0, '0 tool calls'],
    [1, '1 tool call'],
    [4, '4 tool calls'],
  ])('keeps the donor English default for %i calls', (count, label) => {
    mount(count)
    const trigger = screen.getByRole('button', { name: label })
    expect(trigger).toHaveAttribute('data-slot', 'tool-group-trigger')
    expect(within(trigger).getByText(label)).toHaveAttribute('data-slot', 'tool-group-trigger-label')
    expect(trigger.querySelector('[data-slot="tool-group-trigger-loader"]')).toBeNull()
  })

  it.each([0, 1, 4])('calls the supplied formatter with the actual count %i', (count) => {
    const countLabel = vi.fn((value: number) => `${value} localized actions`)
    mount(count, { countLabel })
    const trigger = screen.getByRole('button', { name: `${count} localized actions` })
    expect(trigger).toHaveAttribute('data-slot', 'tool-group-trigger')
    expect(countLabel).toHaveBeenCalledWith(count)
    expect(countLabel).toHaveBeenCalledTimes(1)
    expect(within(trigger).queryByText(/tool calls?/)).toBeNull()
  })

  it('keeps the formatted label and exposes active loading state while a tool runs', async () => {
    mount(2, { countLabel: count => `${count} running actions`, active: true })
    const trigger = screen.getByRole('button', { name: '2 running actions' })
    expect(trigger.querySelector('[data-slot="tool-group-trigger-loader"]')).toBeInTheDocument()
    expect(within(trigger).getByText('2 running actions')).toHaveClass('shimmer')
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Recorded tool output')).toBeInTheDocument()
  })
})
