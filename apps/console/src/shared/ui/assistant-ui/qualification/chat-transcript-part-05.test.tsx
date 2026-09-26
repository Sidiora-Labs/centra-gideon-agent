import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ToolCard } from '../../../../features/chat/ToolCard'
import type { ToolSegment } from '../../../../features/chat/chatTypes'

const failure: ToolSegment = {
  kind: 'tool', id: 'failed-17', tool: 'custom_lookup', detail: 'account 7',
  input: 'lookup account 7', output: 'Permission rejected by upstream', done: true, ok: false,
  agentError: { code: 'ACCESS_DENIED', what: 'Access denied', why: 'No account grant', fix: 'Request access' },
}

describe('real failed tool segment', () => {
  it('shows donor error from the actual agent error without invented retry state', () => {
    const view = render(<ToolCard seg={failure} connected />)
    fireEvent.click(screen.getByRole('button', { name: /Tool Custom lookup.*failed/i }))
    const donor = view.container.querySelector('[data-slot="tool-error"]')
    expect(donor).not.toBeNull()
    expect(donor?.textContent).toContain('Access denied')
    expect(donor?.textContent).not.toMatch(/\d+\/\d+/)
    expect(screen.getByRole('button', { name: 'Retry' }).hasAttribute('disabled')).toBe(true)
    expect(screen.getByRole('button', { name: 'Skip' }).hasAttribute('disabled')).toBe(true)
    expect(view.container.textContent).toContain('No account grant')
    expect(view.container.textContent).toContain('Request access')
    expect(view.container.textContent).toContain('Permission rejected by upstream')
    view.rerender(<ToolCard seg={{ ...failure, ok: undefined }} connected />)
    expect(screen.getByRole('button', { name: /Tool Custom lookup.*failed/i })).toBeTruthy()
    expect(view.container.querySelector('[data-slot="tool-error"]')).not.toBeNull()
  })

  it('keeps a plain failure on the existing result path without repeating its output', () => {
    const view = render(<ToolCard seg={{ ...failure, id: 'failed-18', agentError: undefined,
      output: 'No matching account' }} connected />)
    fireEvent.click(screen.getByRole('button', { name: /Tool Custom lookup.*failed/i }))
    expect(view.container.querySelector('[data-slot="tool-error"]')).toBeNull()
    expect(view.container.textContent?.split('No matching account')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  })
})
