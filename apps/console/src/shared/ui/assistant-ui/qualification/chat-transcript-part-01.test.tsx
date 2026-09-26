import { describe, expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { StreamingIndicator } from '../../chat/StreamingIndicator'
import { ThinkingBlock } from '../../../../features/chat/ThinkingBlock'

describe('live transcript state', () => {
  it('shows only the actual status and activity received from the run', () => {
    vi.useFakeTimers()
    try {
      const view = render(<StreamingIndicator statusText="Reading source" activity="Opened runtime/session.py" />)
      expect(screen.getByTestId('streaming-indicator').textContent).toContain('Reading source')
      expect(screen.getByTestId('streaming-indicator').textContent).toContain('Opened runtime/session.py')
      act(() => vi.advanceTimersByTime(8000))
      expect(screen.getByTestId('streaming-indicator').textContent).toContain('Opened runtime/session.py')
      expect(screen.queryByText('Connecting the dots…')).toBeNull()

      view.rerender(<StreamingIndicator statusText="Calling tool" activity="Checked schema" />)
      expect(screen.getByTestId('streaming-indicator').textContent).toContain('Checked schema')
      expect(screen.getByTestId('streaming-indicator').textContent).not.toContain('Opened runtime/session.py')
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows donor typing feedback only for an actual responding status', () => {
    const view = render(<StreamingIndicator statusText="Responding" activity={null} />)
    expect(screen.getByLabelText('Assistant is typing')).toBeTruthy()
    expect(screen.getByTestId('streaming-indicator').textContent).toContain('Responding')

    view.rerender(<StreamingIndicator statusText="Running tool" activity={null} />)
    expect(screen.queryByLabelText('Assistant is typing')).toBeNull()
    expect(screen.getByTestId('streaming-indicator').textContent).toContain('Running tool')
  })

  it('uses a generic loader only when the run supplies no status or activity', () => {
    vi.useFakeTimers()
    try {
      render(<StreamingIndicator statusText="" activity={null} />)
      expect(screen.getByTestId('streaming-indicator').textContent).toBe('Working')
      act(() => vi.advanceTimersByTime(700))
      expect(screen.getByTestId('streaming-indicator').textContent).toBe('Working')
      expect(screen.queryByText('Almost there…')).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows actual reasoning text through the donor indicator and retains disclosure control', () => {
    render(<ThinkingBlock text="Considered the stored approval result" defaultOpen />)
    const block = screen.getByTestId('thinking-block') as HTMLDetailsElement
    expect(block.open).toBe(true)
    expect(block.querySelector('[data-slot="thinking-indicator"]')).not.toBeNull()
    expect(block.textContent).toContain('Considered the stored approval result')
    expect(block.textContent).not.toContain('confidence')
  })
})
