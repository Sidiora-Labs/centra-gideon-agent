import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { ToolCard } from '../../../../features/chat/ToolCard'
import type { ToolSegment } from '../../../../features/chat/chatTypes'
import { onToolResultFull } from '../../../../features/chat/toolResultBridge'

const tool = (fields: Partial<ToolSegment>): ToolSegment => ({
  kind: 'tool', id: 'call-17', tool: 'custom_lookup', done: false, ...fields,
})

describe('actual tool call rendering', () => {
  it('renders the donor tool call for a generic running call and updates its real result', () => {
    const view = render(<ToolCard seg={tool({ input: 'lookup account', detail: 'account 7' })} />)
    const card = view.container.querySelector('[data-slot="tool-call"]')
    expect(card).not.toBeNull()
    expect(card?.textContent).toContain('Running')
    expect(card?.textContent).toContain('account 7')

    fireEvent.click(screen.getByText('Running Custom lookup'))
    expect(card?.textContent).toContain('lookup account')
    view.rerender(<ToolCard seg={tool({ input: 'lookup account', detail: 'account 7', output: 'Found one account', done: true, ok: true })} />)
    expect(card?.textContent).toContain('Found one account')
    expect(card?.querySelector('[aria-hidden="false"]')?.textContent).toContain('Custom lookup')
    expect(screen.getByText('Running Custom lookup').closest('[aria-hidden]')?.getAttribute('aria-hidden')).toBe('true')
  })

  it('keeps Gideon specialized renderers for native tools', () => {
    const view = render(<ToolCard seg={tool({ tool: 'read_file', inputObj: { path: 'runtime/session.py' }, output: 'class Session: pass', done: true, ok: true })} />)
    expect(view.container.querySelector('[data-slot="tool-call"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Tool Read.*completed/i }))
    expect(view.container.textContent).toContain('runtime/session.py')
    expect(view.container.textContent).toContain('class Session: pass')
  })

  it('does not show a success control for a failed generic tool', () => {
    const view = render(<ToolCard seg={tool({ output: 'Access denied', done: true, ok: false })} />)
    expect(view.container.querySelector('[data-slot="tool-call"]')).toBeNull()
    const trigger = screen.getByRole('button', { name: /failed/i })
    expect(trigger).toBeTruthy()
    fireEvent.click(trigger)
    expect(view.container.textContent).toContain('Access denied')
  })

  it('keeps the compact Gideon card when a generic tool supplies no query detail', () => {
    const view = render(<ToolCard seg={tool({ output: 'done', done: true })} />)
    expect(view.container.querySelector('[data-slot="tool-call"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Tool Custom lookup.*completed/i }))
    expect(view.container.textContent).toContain('done')
  })

  it('summarizes real short command input and keeps a long command in the disclosure', () => {
    const view = render(<ToolCard seg={tool({ input: 'lookup account 7', output: 'found', done: true })} />)
    expect(view.container.querySelector('[data-slot="tool-call"]')?.textContent).toContain('lookup account 7')
    const longInput = 'lookup ' + 'account '.repeat(12)
    view.rerender(<ToolCard seg={tool({ input: longInput, output: 'found', done: true })} />)
    expect(view.container.querySelector('[data-slot="tool-call"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Custom lookup.*Completed/i }))
    expect(view.container.textContent).toContain(longInput)
  })

  it('keeps structured arguments readable without inventing a query', () => {
    const view = render(<ToolCard seg={tool({ inputObj: { path: 'account/7', mode: 'fast' }, done: true })} />)
    expect(view.container.textContent).toContain('path=account/7 · mode=fast')
    view.rerender(<ToolCard seg={tool({ inputObj: { path: 'account/7', filter: 'a'.repeat(90) }, done: true })} />)
    expect(view.container.textContent).toContain('account/7')
    expect(view.container.textContent).not.toContain('filter=')
    view.rerender(<ToolCard seg={tool({ inputObj: { path: 'a'.repeat(90), mode: 'fast' }, done: true })} />)
    expect(view.container.textContent).toContain('a'.repeat(77) + '…')
    view.rerender(<ToolCard seg={tool({ inputObj: { nested: { path: 'account/7' } }, done: true })} />)
    expect(view.container.querySelector('[data-slot="tool-call"]')).toBeNull()
    expect(view.container.textContent).not.toContain('nested=')
  })

  it('preserves purpose and approval metadata when the donor card cannot display them', () => {
    const view = render(<ToolCard seg={tool({ detail: 'account 7', purpose: 'Check a stored receipt', auto: true, done: true, output: 'receipt found' })} />)
    expect(view.container.querySelector('[data-slot="tool-call"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /auto-approved/i }))
    expect(view.container.textContent).toContain('Check a stored receipt')
    expect(view.container.textContent).toContain('receipt found')
  })

  it('keeps the real full-result request bound to the raw result reference', () => {
    const requests: Array<{ rawRef: string; tool: string }> = []
    const stop = onToolResultFull((request) => requests.push(request))
    try {
      render(<ToolCard seg={tool({ tool: 'read_file', output: 'projection', done: true,
        truncated: true, rawRef: 'result-17', originalLength: 2410 })} />)
      fireEvent.click(screen.getByRole('button', { name: /Tool Read.*completed/i }))
      fireEvent.click(screen.getByRole('button', { name: 'Show full result' }))
      expect(requests).toEqual([{ rawRef: 'result-17', tool: 'read_file' }])
    } finally {
      stop()
    }
  })
})

describe('connected tool output from real segments', () => {
  const patch = ['diff --git a/src/account.ts b/src/account.ts', '--- a/src/account.ts', '+++ b/src/account.ts',
    '@@ -1 +1 @@', '-old', '+new'].join('\n')

  it('shows donor CodeDiff for a complete successful single-file patch', () => {
    const view = render(<ToolCard seg={tool({ tool: 'custom_patch', output: patch, done: true, ok: true })} connected />)
    fireEvent.click(view.container.querySelector('[data-slot="tool-fallback-trigger"]') as HTMLButtonElement)
    const diff = view.container.querySelector('[data-slot="code-diff"]')
    expect(diff?.textContent).toContain('src/account.ts')
    expect(diff?.textContent).toContain('+1')
    expect(diff?.textContent).toContain('−1')
    expect(diff?.textContent).toContain('old')
    expect(diff?.textContent).toContain('new')
  })

  it('shows donor TerminalBlock for an actual shell command and output without an exit code', () => {
    const view = render(<ToolCard seg={tool({ tool: 'bash', inputObj: { command: 'pwd' }, output: '/repo\n', done: true, ok: true })} connected />)
    fireEvent.click(view.container.querySelector('[data-slot="tool-fallback-trigger"]') as HTMLButtonElement)
    const terminal = view.container.querySelector('[data-slot="terminal-block"]')
    expect(terminal?.textContent).toContain('pwd')
    expect(terminal?.textContent).toContain('/repo')
    expect(terminal?.textContent).toContain('Finished')
    expect(terminal?.textContent).not.toContain('exit 0')
  })

  it('retains the native bash diff renderer ahead of donor output adapters', () => {
    const view = render(<ToolCard seg={tool({ tool: 'bash', inputObj: { command: 'git diff' }, output: patch, done: true, ok: true })} connected />)
    fireEvent.click(view.container.querySelector('[data-slot="tool-fallback-trigger"]') as HTMLButtonElement)
    expect(view.container.querySelector('[data-slot="code-diff"]')).toBeNull()
    expect(view.container.querySelector('[data-slot="terminal-block"]')).toBeNull()
    expect(view.container.textContent).toContain('old')
    expect(view.container.textContent).toContain('new')
  })
})
