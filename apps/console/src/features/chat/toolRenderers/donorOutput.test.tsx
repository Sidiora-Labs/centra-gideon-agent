import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import type { ToolSegment } from '../chatTypes'
import { renderToolOutput } from './registry'

const change = [
  'diff --git a/src/value.ts b/src/value.ts',
  'index 1234567..abcdef0 100644',
  '--- a/src/value.ts',
  '+++ b/src/value.ts',
  '@@ -1,2 +1,3 @@',
  ' keep',
  '-old',
  '+new',
  '+more',
].join('\n')

function segment(tool: string, output: string, extra: Partial<ToolSegment> = {}): ToolSegment {
  return { kind: 'tool', id: 'recorded-tool', tool, output, done: true, ...extra }
}

function show(seg: ToolSegment) {
  return render(<>{renderToolOutput(seg)}</>)
}

describe('donor output selection from tool records', () => {
  it('shows the real filename, hunk lines, and calculated counts for a complete non-bash diff', () => {
    const view = show(segment('edit_file', change))
    const diff = view.container.querySelector('[data-slot="code-diff"]')
    expect(diff).not.toBeNull()
    expect(within(diff as HTMLElement).getByText('src/value.ts')).toBeInTheDocument()
    expect(within(diff as HTMLElement).getByText('+2')).toBeInTheDocument()
    expect(within(diff as HTMLElement).getByText('−1')).toBeInTheDocument()
    expect(within(diff as HTMLElement).getByText('new')).toBeInTheDocument()
    expect(within(diff as HTMLElement).getByText('old')).toBeInTheDocument()
    expect(within(diff as HTMLElement).getByText('keep')).toBeInTheDocument()
  })

  it('accepts complete classic unified diff and deletion headers without inventing a filename', () => {
    const classic = ['--- a/src/removed.ts', '+++ /dev/null', '@@ -1 +0,0 @@', '-removed'].join('\n')
    const view = show(segment('edit_file', classic))
    expect(view.container.querySelector('[data-slot="code-diff"]')).not.toBeNull()
    expect(screen.getByText('src/removed.ts')).toBeInTheDocument()
    expect(screen.getByText('+0')).toBeInTheDocument()
    expect(screen.getByText('−1')).toBeInTheDocument()
  })

  it('keeps native bash diff output ahead of the donor even for a complete single-file patch', () => {
    const view = show(segment('bash', change, { inputObj: { command: 'git diff' } }))
    expect(view.container.querySelector('[data-slot="code-diff"]')).toBeNull()
    expect(view.container.querySelector('[data-slot="terminal-block"]')).toBeNull()
    expect(screen.getByText('Diff')).toBeInTheDocument()
  })

  it.each([
    ['multiple files', `${change}\ndiff --git a/src/other.ts b/src/other.ts\n--- a/src/other.ts\n+++ b/src/other.ts\n@@ -1 +1 @@\n-a\n+b`],
    ['mismatched paths', change.replace('+++ b/src/value.ts', '+++ b/src/other.ts')],
    ['incomplete hunk', change.slice(0, change.lastIndexOf('\n+more'))],
    ['ambiguous header', '@@ -1 +1 @@\n-before\n+after'],
    ['unexpected trailing text', `${change}\nPatch applied`],
  ])('keeps the content-type fallback for %s', (_case, output) => {
    const view = show(segment('edit_file', output, { contentType: 'diff' }))
    expect(view.container.querySelector('[data-slot="code-diff"]')).toBeNull()
    expect(screen.getByText('Diff')).toBeInTheDocument()
  })

  it.each([
    ['truncated', { truncated: true }],
    ['incomplete tool', { done: false }],
    ['failed tool', { ok: false }],
  ])('does not upgrade a %s diff to the donor view', (_case, extra) => {
    const view = show(segment('edit_file', change, extra))
    expect(view.container.querySelector('[data-slot="code-diff"]')).toBeNull()
    expect(screen.getByText('Diff')).toBeInTheDocument()
  })

  it('uses a recorded JSON command and all actual output lines without claiming an exit code', () => {
    const view = show(segment('mcp__local__exec_command', 'first\nsecond\n', {
      input: JSON.stringify({ command: 'printf first\\nsecond\\n' }),
    }))
    const terminal = view.container.querySelector('[data-slot="terminal-block"]')
    expect(terminal).not.toBeNull()
    expect(within(terminal as HTMLElement).getByText('printf first\\nsecond\\n')).toBeInTheDocument()
    expect(within(terminal as HTMLElement).getByText('first')).toBeInTheDocument()
    expect(within(terminal as HTMLElement).getByText('second')).toBeInTheDocument()
    expect(within(terminal as HTMLElement).getByRole('status')).toHaveTextContent('Finished')
    expect(terminal).not.toHaveTextContent(/exit \d/)
  })

  it('retains the running state and plain command input for a live shell result', () => {
    const view = show(segment('shell', 'building\n', { input: 'make build', done: false }))
    const terminal = view.container.querySelector('[data-slot="terminal-block"]')
    expect(terminal).not.toBeNull()
    expect(within(terminal as HTMLElement).getByText('make build')).toBeInTheDocument()
    expect(within(terminal as HTMLElement).getByText('building')).toBeInTheDocument()
    expect(within(terminal as HTMLElement).queryByRole('status')).toBeNull()
  })

  it.each([
    ['not a shell tool', 'read_file', { input: 'cat notes.txt' }],
    ['missing command', 'bash', {}],
    ['unrecognized input key', 'bash', { inputObj: { query: 'npm test' } }],
    ['JSON array instead of a command', 'bash', { input: '["npm test"]' }],
    ['failed command', 'shell', { input: 'npm test', ok: false }],
    ['truncated output', 'shell', { input: 'npm test', truncated: true }],
  ])('keeps the previous output path for %s', (_case, tool, extra) => {
    const view = show(segment(tool, 'actual output', extra))
    expect(view.container.querySelector('[data-slot="terminal-block"]')).toBeNull()
    expect(screen.getByText('actual output')).toBeInTheDocument()
  })
})
