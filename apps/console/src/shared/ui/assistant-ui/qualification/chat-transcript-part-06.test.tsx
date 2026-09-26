import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ThinkingBlock } from '../../../../features/chat/ThinkingBlock'
import { ToolCard } from '../../../../features/chat/ToolCard'
import type { ToolSegment } from '../../../../features/chat/chatTypes'

const savedTool: ToolSegment = {
  kind: 'tool', id: 'read-7', tool: 'read_file', inputObj: { path: 'runtime/session.py' },
  output: 'class Session: pass', done: true, ok: true,
}

describe('connected transcript donor disclosures', () => {
  it('renders actual reasoning text once through the donor disclosure', () => {
    const view = render(<ThinkingBlock text="Checked the persisted session state" defaultOpen connected />)
    expect(view.container.querySelectorAll('[data-slot="reasoning-root"]')).toHaveLength(1)
    expect(view.container.querySelectorAll('[data-slot="reasoning-content"]')).toHaveLength(1)
    expect(view.container.querySelector('[data-slot="reasoning-trigger-label"]')?.textContent).toBe('Thinking')
    expect(screen.getByText('Checked the persisted session state')).toBeTruthy()
    expect(view.container.querySelector('details')).toBeNull()
    fireEvent.click(view.container.querySelector('[data-slot="reasoning-trigger"]') as HTMLButtonElement)
    expect(view.container.querySelector('[data-slot="reasoning-root"]')?.getAttribute('data-open')).toBeNull()
  })

  it('keeps native tool input and result inside one donor fallback disclosure', () => {
    const view = render(<ToolCard seg={savedTool} connected />)
    expect(view.container.querySelectorAll('[data-slot="tool-fallback-root"]')).toHaveLength(1)
    expect(view.container.querySelector('[data-slot="tool-fallback-trigger"]')).not.toBeNull()
    expect(view.container.querySelector('[data-slot="tool-call"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Tool Read.*completed/i }))
    expect(view.container.textContent).toContain('runtime/session.py')
    expect(view.container.textContent).toContain('class Session: pass')
  })

  it('preserves segment order and failure metadata in connected mode', () => {
    const failed: ToolSegment = { ...savedTool, id: 'failed-8', ok: false,
      agentError: { code: 'ACCESS_DENIED', what: 'The file is blocked', why: 'No grant', fix: 'Request access' },
      recoveryHints: ['Ask the owner for access'],
    }
    const view = render(<>
      <ThinkingBlock text="Checked authorization" connected />
      <ToolCard seg={failed} connected />
    </>)
    expect(view.container.querySelector('[data-slot="reasoning-root"]')?.compareDocumentPosition(
      view.container.querySelector('[data-slot="tool-fallback-root"]') as Node,
    )).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    fireEvent.click(screen.getByRole('button', { name: /Tool Read.*failed/i }))
    expect(view.container.textContent).toContain('ACCESS_DENIED')
    expect(view.container.textContent).toContain('Ask the owner for access')
    expect(view.container.querySelector('[data-slot="tool-fallback-duration"]')).toBeNull()
  })

  it('updates donor tool status from the same live segment without invented duration', () => {
    const view = render(<ToolCard seg={{ ...savedTool, done: false, output: undefined }} connected />)
    const icon = view.container.querySelector('[data-slot="tool-fallback-trigger-icon"]')
    expect(icon?.getAttribute('class')).toContain('animate-spin')
    expect(view.container.querySelector('[data-slot="tool-fallback-duration"]')).toBeNull()
    view.rerender(<ToolCard seg={savedTool} connected />)
    expect(view.container.querySelector('[data-slot="tool-fallback-trigger-icon"]')?.getAttribute('class')).not.toContain('animate-spin')
    expect(view.container.querySelector('[data-slot="tool-fallback-duration"]')).toBeNull()
  })
})
