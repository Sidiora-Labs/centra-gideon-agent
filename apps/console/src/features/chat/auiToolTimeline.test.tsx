import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ToolSegment } from './chatTypes'
import { ToolCard } from './ToolCard'
import { AuiToolProgress, toolTimelineSteps } from './auiToolTimeline'

const tool = (id: string, name: string, extra: Partial<ToolSegment> = {}): ToolSegment => ({
  kind: 'tool', id, tool: name, done: true, ...extra,
})
const bash = tool('run-1', 'bash', {
  input: JSON.stringify({ command: 'npm test' }), output: '19 passed', done: false,
})
const read = tool('read-1', 'read_file', {
  inputObj: { path: 'src/app.ts' }, output: 'file contents', done: true,
})

function show(tools: ToolSegment[], streaming = false) {
  return render(<AuiToolProgress tools={tools} streaming={streaming}>
    {tools.map(seg => <ToolCard key={seg.id} seg={seg} />)}
  </AuiToolProgress>)
}

afterEach(cleanup)

describe('real ToolSegment donor progress', () => {
  it('preserves order, source labels, input chips, and recorded status without inferred data', () => {
    const failed = tool('read-2', 'read_file', { inputObj: { path: 'src/app.ts' }, output: 'permission denied', ok: false })
    const steps = toolTimelineSteps([bash, read, failed])
    expect(steps.map(step => step.verb)).toEqual([
      'Run command running', 'Read completed', 'Read failed',
    ])
    expect(steps.map(step => step.chip)).toEqual([
      'npm test', 'src/app.ts', 'src/app.ts · 2',
    ])
    expect(steps.every(step => typeof step.icon === 'function' || typeof step.icon === 'object')).toBe(true)
  })

  it('derives chips only from actual detail or input and handles repeated, long, and unstructured calls', () => {
    const steps = toolTimelineSteps([
      tool('a', 'grep', { detail: 'match in src/app.ts', input: 'ignored' }),
      tool('b', 'bash', { input: 'echo ready', done: false }),
      tool('c', 'bash', { input: '["not a command"]' }),
      tool('d', 'bash', { input: '{bad json}' }),
      tool('e', 'grep', { inputObj: { query: 'a'.repeat(60) }, agentError: { code: 'E', what: 'Denied', why: '', fix: '' } }),
      tool('f', '', {}),
    ])
    expect(steps.map(step => step.chip)).toEqual([
      'match in src/app.ts', 'echo ready', 'bash', 'bash · 2', `${'a'.repeat(47)}…`, 'Tool',
    ])
    expect(steps[4]?.verb).toBe('Search code failed')
    expect(steps[5]?.verb).toBe('Tool completed')
  })

  it('shows ordered donor timeline steps during real unfinished work and retains each rich card once', () => {
    const view = show([bash, read], true)
    expect(view.container.querySelector('[data-slot="aui-tool-progress"]')).not.toBeNull()
    expect(view.container.querySelector('[data-slot="tool-timeline"]')).not.toBeNull()
    expect(view.container.querySelector('[data-slot="tool-group-root"]')).toBeNull()
    expect(screen.getAllByRole('button', { name: /Run command/ })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /Read/ })).toHaveLength(1)
    fireEvent.click(within(view.container.querySelector('[data-slot="tool-timeline"]') as HTMLElement).getByRole('button'))
    const timeline = view.container.querySelector('[data-slot="tool-timeline"]')!
    expect(timeline.textContent?.indexOf('Run command running')).toBeLessThan(timeline.textContent!.indexOf('Read completed'))
    expect(timeline).toHaveTextContent('npm test')
    expect(timeline).toHaveTextContent('src/app.ts')
    expect(timeline).not.toHaveTextContent(/\+\d+|−\d+|\d+ms|exit \d+/)
  })

  it('uses one donor ToolGroup for completed multiple calls and preserves rich outputs', () => {
    const finished = { ...bash, done: true }
    const view = show([finished, read])
    expect(view.container.querySelector('[data-slot="tool-timeline"]')).toBeNull()
    expect(view.container.querySelectorAll('[data-slot="tool-group-root"]')).toHaveLength(1)
    fireEvent.click(within(view.container.querySelector('[data-slot="tool-group-root"]') as HTMLElement)
      .getByRole('button', { name: '2 tool calls' }))
    expect(screen.getAllByRole('button', { name: /Run command/ })).toHaveLength(1)
    expect(screen.getAllByRole('button', { name: /Read/ })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: /Run command/ }))
    expect(screen.getByText('19 passed')).toBeInTheDocument()
  })

  it('uses a supplied count label only for the completed multi-call disclosure', () => {
    const finished = { ...bash, done: true }
    const view = render(<AuiToolProgress tools={[finished, read]} streaming={false} countLabel={(count) => `Recorded calls: ${count}`}>
      <span>Recorded output</span>
    </AuiToolProgress>)
    expect(view.container.querySelectorAll('[data-slot="tool-group-root"]')).toHaveLength(1)
    expect(screen.getByRole('button', { name: 'Recorded calls: 2' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Recorded calls: 2' }))
    expect(screen.getAllByText('Recorded output')).toHaveLength(1)
  })

  it('keeps a single completed call on the timeline without claiming it is running', () => {
    const view = show([read])
    expect(view.container.querySelector('[data-slot="tool-timeline"]')).not.toBeNull()
    expect(view.container.querySelector('[data-slot="tool-group-root"]')).toBeNull()
    expect(screen.getByText('1 tool call')).toBeInTheDocument()
    expect(screen.queryByText('1 tool call running')).not.toHaveClass('shimmer')
    expect(screen.getAllByRole('button', { name: /Read/ })).toHaveLength(1)
  })

  it('uses a neutral label while a response streams after all recorded tools finished', () => {
    const view = show([read, { ...bash, done: true }], true)
    expect(view.container.querySelector('[data-slot="tool-timeline"]')).not.toBeNull()
    expect(view.container.querySelector('[data-slot="tool-group-root"]')).toBeNull()
    expect(screen.getByText('2 tool calls')).toBeInTheDocument()
    expect(screen.queryByText('2 tool calls running')).not.toHaveClass('shimmer')
  })

  it('does not create a donor disclosure or invented step when there are no tools', () => {
    const view = render(<AuiToolProgress tools={[]} streaming={false}>
      <span>Existing child</span>
    </AuiToolProgress>)
    expect(view.container.querySelector('[data-slot="aui-tool-progress"]')).toHaveTextContent('Existing child')
    expect(view.container.querySelector('[data-slot="tool-timeline"]')).toBeNull()
    expect(view.container.querySelector('[data-slot="tool-group-root"]')).toBeNull()
  })
})
