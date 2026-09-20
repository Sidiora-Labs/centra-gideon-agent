import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EscalationPanel } from './EscalationPanel'

describe('EscalationPanel', () => {
  it('explains the reason, non-duplicated cause, and every attempt', () => {
    render(<EscalationPanel error="node failed" escalation={{
      kind: 'escalation', reason: 'repeated_error', detail: 'provider refused the request',
      attempts: [
        { attempt: 1, failure_class: 'permission', error_signature: 'abc123', fix_instruction: 'Grant read access.' },
        { attempt: 2, failure_class: 'permission', error_signature: 'def456', fix_instruction: 'Use an allowed source.' },
      ],
    }} />)
    expect(screen.getByText('The same error kept recurring.')).toBeTruthy()
    expect(screen.getByText('Cause: provider refused the request')).toBeTruthy()
    expect(screen.getByText('Attempt 1 · permission')).toBeTruthy()
    expect(screen.getByText('Attempt 2 · permission')).toBeTruthy()
    expect(screen.getByText('Suggested fix: Grant read access.')).toBeTruthy()
  })

  it('does not repeat a cause already carried by the run error', () => {
    render(<EscalationPanel error="Failed: provider refused the request" escalation={{
      kind: 'escalation', reason: 'repeated_error', detail: 'provider refused the request',
    }} />)
    expect(screen.queryByText(/Cause:/)).toBeNull()
  })
})
