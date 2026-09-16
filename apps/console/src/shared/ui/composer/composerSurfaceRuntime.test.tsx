import { createRef, useState } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { beforeEach, describe, expect, it } from 'vitest'
import { writeQuery } from '../../data/data'
import { Composer } from '../Composer'
import { ComposerStage } from '../ComposerStage'
import { AgentPill, ModelPill, NaturalVoicePill, PlusMenu, ReasoningPill } from './controls'
import { agentChoices, agentEfforts, contextIndicator, modelChoices, naturalVoiceLabel } from './composerChoices'
import { COMPOSER_HEIGHT, ComposerFileDrop, ComposerResizeSession, composerAction, keyboardComposerHeight, restoredComposerHeight } from './composerSurfaceState'
import type { ComposerData, ComposerProps, ComposerValue } from './types'

const roster: ComposerData = {
  agents: [{ name: 'Gideon' }, { name: 'Researcher' }], providers: [],
  models: [{ name: 'provider:model-a', model_name: 'Model A', description: '', provider: 'Provider' }],
  discovered: { 'runtime:code': [{ id: 'external', name: 'Code agent', runtime: 'runtime:code', description: 'Repository tasks -- ⚠️ DO NOT EDIT',
    provider_agent: 'code', reasoning_effort: '', models: ['code-model'], supported_efforts: [{ value: 'high', label: 'High' }] }] },
}
const selection: ComposerValue = { agent: 'Gideon', model: 'Auto', approval: 'normal', reasoning: '', taskMode: 'agent' }

beforeEach(() => {
  localStorage.removeItem(COMPOSER_HEIGHT.key)
  writeQuery('chat:send-on-enter', true, true)
})

describe('composer interaction models', () => {
  it.each([['48', 48], ['480', 480], ['120.5', 120.5], ['47', 92], ['481', 92], [null, 92], ['NaN', 92], ['Infinity', 92]])('restores height %s as %s', (raw, expected) => {
    expect(restoredComposerHeight(raw as string | null)).toBe(expected)
  })
  it('anchors resize math to the press and clamps both bounds', () => {
    const resize = new ComposerResizeSession()
    expect(resize.move(10)).toBeUndefined()
    resize.begin(200, 92)
    expect(resize.move(160)).toBe(132)
    expect(resize.move(300)).toBe(48)
    expect(resize.move(-1000)).toBe(480)
    resize.end()
    expect(resize.move(100)).toBeUndefined()
  })
  it('offers equivalent bounded keyboard resizing', () => {
    expect(keyboardComposerHeight('ArrowUp', 92)).toBe(108)
    expect(keyboardComposerHeight('ArrowDown', 92, true)).toBe(48)
    expect(keyboardComposerHeight('Home', 200)).toBe(48)
    expect(keyboardComposerHeight('End', 200)).toBe(480)
    expect(keyboardComposerHeight('Escape', 200)).toBeUndefined()
  })
  it('owns nested file enters and emits original File objects in order', () => {
    const drop = new ComposerFileDrop()
    expect(drop.enter(['text/plain'])).toBe(false)
    expect(drop.enter(['Files'])).toBe(true)
    expect(drop.enter(['Files'])).toBe(true)
    expect(drop.leave()).toBe(true)
    const first = new File(['a'], 'one.txt', { type: 'text/plain' })
    const second = new File(['b'], 'two.txt', { type: 'text/plain' })
    const files = drop.receive([first, second])
    expect(files).toEqual([first, second])
    expect(files[0]).toBe(first)
    expect(drop.leave()).toBe(false)
    drop.enter(['Files']); drop.reset()
    expect(drop.leave()).toBe(false)
  })
  it.each([
    [{ processing: true, streaming: false, canQueue: false, justSent: false }, false],
    [{ processing: false, streaming: true, canQueue: false, justSent: false }, false],
    [{ processing: false, streaming: true, canQueue: true, justSent: false }, true],
    [{ processing: false, streaming: false, canQueue: false, justSent: true }, false],
  ])('ties keyboard submission to the visible action %#', (state, allowed) => {
    expect(composerAction({ ...state, canSend: true }).canSubmit).toBe(allowed)
  })
})

describe('choice projections', () => {
  it('filters native names and discovered runtime/description without changing wire values', () => {
    const choices = agentChoices(roster, 'runtime:CODE')
    expect(choices.groups.flatMap(group => group.rows.map(row => row.value))).toEqual(['Code agent'])
    expect(choices.groups[1].rows[0].hint).toBe('Repository tasks')
    expect(agentChoices(roster, 'research').groups[0].rows[0].value).toBe('Researcher')
    expect(agentChoices(roster, 'missing').noMatches).toBe(true)
  })
  it('retains the search threshold across discovered and native agents', () => {
    const eight = { ...roster, discovered: {}, agents: Array.from({ length: 8 }, (_, index) => ({ name: `Agent ${index}` })) }
    expect(agentChoices(eight, '').search).toBe(false)
    expect(agentChoices({ ...eight, discovered: roster.discovered }, '').search).toBe(true)
  })
  it('scopes models and effort choices to the selected discovered agent', () => {
    expect(modelChoices(roster, 'Code agent', 'code-model').options.map(row => row.value)).toEqual(['Auto', 'code-model'])
    expect(modelChoices(roster, 'Gideon', 'provider:model-a').label).toBe('Model A')
    expect(agentEfforts(roster, 'Code agent')).toEqual([{ value: 'high', label: 'High' }])
    expect(agentEfforts(roster, 'Gideon').map(row => row.value)).toEqual(['low', 'medium', 'high', 'max'])
  })
  it('keeps unknown context separate from zero and clamps measured limits', () => {
    expect(contextIndicator(undefined)).toBeNull()
    expect(contextIndicator(NaN)).toBeNull()
    expect(contextIndicator(0)?.label).toBe('Context: 0% used')
    expect(contextIndicator(-10)?.value).toBe(0)
    expect(contextIndicator(70)?.tone).toBe('warn')
    expect(contextIndicator(90)?.tone).toBe('danger')
    expect(contextIndicator(150)?.remaining).toBe(0)
  })
  it.each([
    ['', false, '', 'Agent default'], ['on', false, '', 'Plain'], ['off', false, '', 'Off'],
    ['on', false, 'conversation', 'Default'], ['', true, 'agent', 'Plain (agent)'], ['off', true, 'conversation', 'Plain'],
  ] as const)('reports backend voice attribution for %s/%s/%s', (choice, effective, source, label) => {
    expect(naturalVoiceLabel(choice, effective, source)).toBe(label)
  })
})

describe('real composer surface', () => {
  function mount(options: Partial<ComposerProps> = {}) {
    const sent: string[] = []
    let stops = 0
    let optimizations = 0
    function Host({ config }: { config: Partial<ComposerProps> }) {
      const [value, setValue] = useState(config.value ?? 'Hello Gideon')
      return <Composer value={value} onChange={setValue} onSend={() => sent.push(value)} onStop={() => { stops++ }}
        onOptimize={() => { optimizations++ }} controls={{}} {...config} />
    }
    const result = render(<Host config={options} />)
    return { ...result, sent, stops: () => stops, optimizations: () => optimizations,
      configure: (config: Partial<ComposerProps>) => result.rerender(<Host config={config} />) }
  }

  it('submits the current CodeMirror draft and makes its sent state inert', () => {
    const host = mount()
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'Current draft' } }))
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(host.sent).toEqual(['Current draft'])
    expect(screen.getByRole('button', { name: 'Sent' })).toBeInTheDocument()
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(host.sent).toHaveLength(1)
    host.unmount()
  })

  it('keeps button and keyboard inert during processing, then offers stop and steer', () => {
    const host = mount({ processing: true })
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    expect(screen.getByRole('button', { name: 'Processing…' })).toHaveAttribute('aria-busy', 'true')
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(host.sent).toEqual([])
    host.configure({ streaming: true })
    fireEvent.keyDown(editor, { key: 'Enter' })
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(host.sent).toEqual([])
    expect(host.stops()).toBe(1)
    host.configure({ streaming: true, canQueue: true })
    fireEvent.click(screen.getByRole('button', { name: 'Steer — send into the running turn' }))
    expect(host.sent).toEqual(['Hello Gideon'])
    expect(screen.queryByRole('button', { name: 'Sent' })).toBeNull()
    host.unmount()
  })

  it('enforces minChars, then reads the shared Enter preference without a request double', () => {
    writeQuery('chat:send-on-enter', false, true)
    const host = mount({ value: 'abc', minChars: 5 })
    const send = screen.getByRole('button', { name: 'Send message' })
    expect(send).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(send)
    expect(host.sent).toEqual([])
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(EditorView.findFromDOM(editor)!.state.doc.toString()).toContain('\n')
    expect(host.sent).toEqual([])
    host.unmount()
  })

  it('restores and persists resize changes through the accessible handle', () => {
    localStorage.setItem(COMPOSER_HEIGHT.key, '160')
    const host = mount()
    const resize = screen.getByRole('separator', { name: 'Resize message input' })
    expect(resize).toHaveAttribute('aria-valuenow', '160')
    fireEvent.keyDown(resize, { key: 'ArrowUp' })
    expect(resize).toHaveAttribute('aria-valuenow', '176')
    expect(localStorage.getItem(COMPOSER_HEIGHT.key)).toBe('176')
    fireEvent.keyDown(resize, { key: 'Home' })
    expect(resize).toHaveAttribute('aria-valuenow', '48')
    host.unmount()
  })

  it('ends pointer resize ownership on cancellation and unmount', () => {
    const host = mount()
    const resize = screen.getByRole('separator', { name: 'Resize message input' })
    fireEvent(resize, new MouseEvent('pointerdown', { bubbles: true, clientY: 200, button: 0 }))
    fireEvent(window, new MouseEvent('pointermove', { clientY: 160 }))
    expect(resize).toHaveAttribute('aria-valuenow', '132')
    fireEvent(window, new Event('pointercancel'))
    fireEvent(window, new MouseEvent('pointermove', { clientY: 120 }))
    expect(resize).toHaveAttribute('aria-valuenow', '132')
    host.unmount()
    fireEvent(window, new MouseEvent('pointermove', { clientY: 0 }))
    expect(localStorage.getItem(COMPOSER_HEIGHT.key)).toBe('132')
  })

  it('keeps optimizer progress and screen reasons outside their accessible names', () => {
    const transitions: string[] = []
    const host = mount({ controls: { optimize: true }, optimizing: true,
      screenShare: { available: true, sharing: false, disabledReason: 'Choose a vision model', onToggle: () => transitions.push('share') } })
    expect(screen.getByRole('button', { name: 'Optimize prompt (⌘↵)' })).toHaveAttribute('aria-busy', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Optimize prompt (⌘↵)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Share screen' }))
    expect(host.optimizations()).toBe(0)
    expect(transitions).toEqual([])
    expect(screen.getByRole('button', { name: 'Share screen' })).toHaveAttribute('aria-disabled', 'true')
    host.unmount()
  })

  it('forwards stage refs to the measured shared-layout surface', () => {
    const ref = createRef<HTMLDivElement>()
    const sent: string[] = []
    const host = render(<ComposerStage ref={ref} value="Draft" onChange={value => sent.push(value)} onSend={() => sent.push('send')} controls={{}} />)
    expect(ref.current).toHaveAttribute('data-gideon-composer-stage', 'true')
    expect(ref.current?.querySelector('[data-gideon-composer]')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(sent).toEqual(['send'])
    host.unmount()
  })
})

describe('actual selection menus', () => {
  it('filters a long roster and returns the selected native wire name', () => {
    const picks: string[] = []
    const data = { ...roster, agents: [...roster.agents, ...Array.from({ length: 8 }, (_, index) => ({ name: `Worker ${index}` }))] }
    const host = render(<AgentPill data={data} value="Gideon" onSelect={value => picks.push(value)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Agent: Gideon' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Search agents' }), { target: { value: 'research' } })
    fireEvent.click(screen.getByRole('button', { name: 'Researcher' }))
    expect(picks).toEqual(['Researcher'])
    expect(screen.getByRole('button', { name: 'Agent: Gideon' })).toHaveAttribute('aria-expanded', 'false')
    host.unmount()
  })

  it('opens a scoped model list from its signal and restores focus after a choice', () => {
    const picks: string[] = []
    const props = { data: roster, agent: 'Code agent', value: 'Auto', onSelect: (value: string) => picks.push(value) }
    const host = render(<ModelPill {...props} openSignal={0} />)
    host.rerender(<ModelPill {...props} openSignal={1} />)
    expect(screen.queryByRole('button', { name: /Model A/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^code-model\s*runtime:code$/ }))
    expect(picks).toEqual(['code-model'])
    expect(screen.getByRole('button', { name: 'Model: Auto' })).toHaveFocus()
    host.unmount()
  })

  it('carries caller selections back through composer patches', () => {
    const patches: Partial<ComposerValue>[] = []
    const host = render(<Composer value="Draft" onChange={value => patches.push({ agent: value })} onSend={() => patches.push({})}
      data={roster} selection={selection} controls={{ approval: true }} onSelect={patch => patches.push(patch)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Permission mode: Normal' }))
    fireEvent.click(screen.getByRole('button', { name: /^Trust reads\s*Auto-approve read-only$/ }))
    expect(patches).toEqual([{ approval: 'trust_reads' }])
    host.unmount()
  })

  it('shows backend voice attribution and permits an explicit off override', () => {
    const picks: string[] = []
    const host = render(<NaturalVoicePill choice="" effective source="agent" agentDefault onSelect={value => picks.push(value)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Natural voice: Plain (agent)' }))
    fireEvent.click(screen.getByRole('button', { name: /^Off\s*Standard prose here, even if the agent asks for plainer$/ }))
    expect(picks).toEqual(['off'])
    host.unmount()
  })

  it('exposes saved prompts and caller extras while retaining a direct attach-only path', () => {
    const actions: string[] = []
    const host = render(<PlusMenu onAttach={() => actions.push('attach')} />)
    fireEvent.click(screen.getByRole('button', { name: 'Attach files' }))
    host.rerender(<PlusMenu onAttach={() => actions.push('attach')} onOpenPrompts={() => actions.push('prompts')}
      extra={close => <button onClick={() => { actions.push('extra'); close() }}>Custom action</button>} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add to message' }))
    fireEvent.click(screen.getByRole('button', { name: /^Saved prompts\s*Insert a saved prompt$/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Add to message' }))
    fireEvent.click(screen.getByRole('button', { name: 'Custom action' }))
    expect(actions).toEqual(['attach', 'prompts', 'extra'])
    host.unmount()
  })

  it('offers Default plus backend reasoning values and never invents an effort axis', () => {
    const picks: string[] = []
    const host = render(<ReasoningPill value="high" efforts={agentEfforts(roster, 'Code agent')} onSelect={value => picks.push(value)} />)
    fireEvent.click(screen.getByRole('button', { name: 'Reasoning effort: High' }))
    fireEvent.click(screen.getByRole('button', { name: 'Default' }))
    expect(picks).toEqual([''])
    host.rerender(<ReasoningPill value="" efforts={[]} onSelect={value => picks.push(value)} />)
    expect(screen.queryByRole('button')).toBeNull()
    host.unmount()
  })
})
