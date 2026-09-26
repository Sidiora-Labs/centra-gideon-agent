import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MessageAssistant } from '../../chat/MessageAssistant'
import { MessageUser } from '../../chat/MessageUser'
import { clockTime } from '../../../data/epoch'

const savedAt = '2025-06-18T12:35:00Z'

describe('persisted message timing', () => {
  it('renders the authoritative timestamp from the assistant turn', () => {
    const view = render(<MessageAssistant timestamp={savedAt} actions={<button type="button">Real actions</button>}>Stored answer</MessageAssistant>)
    const timing = view.container.querySelector('time')
    expect(timing).not.toBeNull()
    expect(timing?.textContent).toContain(clockTime(savedAt))
    expect(screen.getByText('Stored answer')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Real actions' })).toBeTruthy()
  })

  it('renders the same persisted timestamp for a saved user turn while preserving its body', () => {
    const view = render(<MessageUser timestamp={savedAt}>Open the project file</MessageUser>)
    const timing = view.container.querySelector('time')
    expect(timing?.textContent).toContain(clockTime(savedAt))
    expect(view.container.textContent).toContain('Open the project file')
  })

  it('does not invent a timestamp when the turn has none or an invalid value', () => {
    const view = render(<MessageAssistant>Streaming answer</MessageAssistant>)
    expect(view.container.querySelector('time')).toBeNull()
    view.rerender(<MessageAssistant timestamp="not a date">Streaming answer</MessageAssistant>)
    expect(view.container.querySelector('time')).toBeNull()
    view.rerender(<MessageAssistant timestamp={savedAt}>Saved answer</MessageAssistant>)
    expect(view.container.querySelector('time')).not.toBeNull()
  })

  it('shows donor logo only for a model identified by the bound session', () => {
    const view = render(<MessageAssistant model="claude-sonnet-4">Answer</MessageAssistant>)
    expect(view.container.querySelector('svg')).not.toBeNull()
    expect(view.container.textContent).toContain('claude-sonnet-4')
    view.rerender(<MessageAssistant model="gemini-2.5-pro">Answer</MessageAssistant>)
    expect(view.container.querySelector('svg')).not.toBeNull()
    expect(view.container.textContent).toContain('gemini-2.5-pro')
    view.rerender(<MessageAssistant model="gpt-5">Answer</MessageAssistant>)
    expect(view.container.querySelector('svg')).not.toBeNull()
    expect(view.container.textContent).toContain('gpt-5')
    view.rerender(<MessageAssistant model="Auto">Answer</MessageAssistant>)
    expect(view.container.querySelector('svg')).toBeNull()
    expect(view.container.textContent).not.toContain('Auto')
  })

  it('submits the entered downvote reason through the supplied persistent callback', () => {
    const submitted: Array<{ verdict: string; reason?: string }> = []
    const closed: string[] = []
    const view = render(<MessageAssistant feedback={{ verdict: 'down', busy: false,
      onSubmit: (verdict, reason) => submitted.push({ verdict, reason }),
      onClose: () => closed.push('closed'),
    }}>Saved answer</MessageAssistant>)
    expect(view.container.querySelector('[data-slot="feedback-dialog"]')).not.toBeNull()
    fireEvent.change(screen.getByLabelText('Anything else?'), { target: { value: '  Missing source  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send feedback' }))
    expect(submitted).toEqual([{ verdict: 'down', reason: 'Missing source' }])
    fireEvent.click(screen.getByRole('button', { name: 'Cancel feedback' }))
    expect(closed).toEqual(['closed'])
  })

  it('exposes vote controls only when the caller supplies persistent actions', () => {
    const calls: string[] = []
    const view = render(<MessageAssistant onFeedbackUp={() => calls.push('up')}
      onFeedbackDown={() => calls.push('down')} feedbackVerdict="up">Saved answer</MessageAssistant>)
    const up = screen.getByRole('button', { name: 'Mark response helpful' })
    const down = screen.getByRole('button', { name: 'Mark response unhelpful' })
    expect(up.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(up)
    fireEvent.click(down)
    expect(calls).toEqual(['up', 'down'])
    view.rerender(<MessageAssistant feedbackBusy onFeedbackUp={() => calls.push('duplicate')}>Saved answer</MessageAssistant>)
    expect(screen.getByRole('button', { name: 'Mark response helpful' }).hasAttribute('disabled')).toBe(true)
    view.rerender(<MessageAssistant>Saved answer</MessageAssistant>)
    expect(screen.queryByRole('button', { name: 'Mark response helpful' })).toBeNull()
  })

  it('shows server error and blocks a duplicate submit while saving', () => {
    const submitted: string[] = []
    const view = render(<MessageAssistant onFeedbackUp={() => submitted.push('up')} feedback={{ verdict: 'down', busy: true, error: 'Feedback save failed',
      onSubmit: () => submitted.push('sent'), onClose: () => submitted.push('closed'),
    }}>Saved answer</MessageAssistant>)
    expect(screen.getByRole('alert').textContent).toBe('Feedback save failed')
    expect(screen.getByText('Saving feedback…')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Send feedback' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Cancel feedback' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'Mark response helpful' }).hasAttribute('disabled')).toBe(true)
    expect(submitted).toEqual([])
    view.rerender(<MessageAssistant>Saved answer</MessageAssistant>)
    expect(view.container.querySelector('[data-slot="feedback-dialog"]')).toBeNull()
  })
})
