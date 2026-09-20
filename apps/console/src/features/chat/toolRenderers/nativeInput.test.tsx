import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { inputOf, labelForTool, renderToolInput, renderToolOutput } from './registry'
import { NATIVE_TOOL_REGISTRY } from './native'
import type { ToolSegment } from '../chatTypes'


const fromHistory = (tool: string, input: unknown, output?: string): ToolSegment => ({
  kind: 'tool', id: 't1', tool, input: JSON.stringify(input), output, done: true,
})

const fromLiveFrame = (tool: string, input: Record<string, unknown>, output?: string): ToolSegment => ({
  kind: 'tool', id: 't1', tool, input: JSON.stringify(input), inputObj: input, output, done: true,
})

const html = (node: React.ReactNode): string => render(<>{node}</>).container.innerHTML

describe('native INPUT overrides resolve the input from persisted history (#682)', () => {
  it('has one input owner for object and string-shaped calls', () => {
    const value = { command: 'make test' }
    expect(inputOf(fromLiveFrame('bash', value))).toBe(value)
    expect(inputOf(fromHistory('bash', value))).toEqual(value)
  })

  it.each([
    ['bash', { command: 'ls -la' }, 'Command', 'ls -la'],
    ['read_file', { path: '/a/b.py' }, 'File', '/a/b.py'],
    ['write_file', { path: '/a/b.py', content: 'x' }, 'File', '/a/b.py'],
    ['glob', { pattern: '**/*.ts' }, 'Pattern', '**/*.ts'],
    ['list_dir', { path: '/a' }, 'Pattern', '/a'],
    ['grep', { query: 'needle' }, 'Query', 'needle'],
    ['web_fetch', { url: 'https://example.com/p' }, 'URL', 'https://example.com/p'],
  ])('%s renders its chip label from a JSON-string input', (tool, input, label, primary) => {
    const out = html(renderToolInput(fromHistory(tool, input)))
    expect(out).toContain(label)
    expect(out).toContain(primary)
  })

  it('edit_file renders the old→new mini-diff from a JSON-string input', () => {
    const out = html(renderToolInput(
      fromHistory('edit_file', { path: '/a/b.py', old_str: 'before', new_str: 'after' }),
    ))
    expect(out).toContain('Change')
    expect(out).toContain('-before')
    expect(out).toContain('+after')
  })

  it('a live-frame segment still renders the same chip (no regression)', () => {
    const out = html(renderToolInput(fromLiveFrame('bash', { command: 'make test' })))
    expect(out).toContain('Command')
    expect(out).toContain('make test')
  })

  it('a non-JSON scalar input still falls through to the raw fallback', () => {
    const seg: ToolSegment = { kind: 'tool', id: 't1', tool: 'bash', input: 'ls -la', done: true }
    const out = html(renderToolInput(seg))
    expect(out).toContain('Input')
    expect(out).toContain('ls -la')
    expect(out).not.toContain('Command')
  })
})

describe('the tool card rail is keyed by registry metadata', () => {
  it.each([
    ['bash', 'Run command', 'Command'],
    ['read_file', 'Read', 'File'],
    ['grep', 'Search code', 'Query'],
    ['web_fetch', 'Fetch page', 'URL'],
  ])('%s supplies its card title and body label from one entry', (tool, title, body) => {
    expect(labelForTool(fromHistory(tool, {}))).toBe(NATIVE_TOOL_REGISTRY[tool].label)
    expect(NATIVE_TOOL_REGISTRY[tool]).toMatchObject({ label: title, inputLabel: body })
  })
})

describe('native OUTPUT overrides that read the call input resolve it too (#682)', () => {
  it('web_fetch titles the card with the URL from a JSON-string input', () => {
    const out = html(renderToolOutput(
      fromHistory('web_fetch', { url: 'https://example.com/p' }, '# Page\n\nbody'),
    ))
    expect(out).toContain('Fetched page')
    expect(out).toContain('https://example.com/p')
  })

  it('web_fetch on a live frame still shows the URL (no regression)', () => {
    const out = html(renderToolOutput(
      fromLiveFrame('web_fetch', { url: 'https://example.com/p' }, '# Page\n\nbody'),
    ))
    expect(out).toContain('https://example.com/p')
  })

  it('web_fetch without a resolvable URL still renders the page body', () => {
    const seg: ToolSegment = { kind: 'tool', id: 't1', tool: 'web_fetch', input: 'not json', output: 'body text', done: true }
    const out = html(renderToolOutput(seg))
    expect(out).toContain('Fetched page')
    expect(out).toContain('body text')
  })
})
