import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Composer } from '../../Composer'
import { writeQuery } from '../../../data/data'
import { api } from '../../../data/api'
import { mentionResults, mentionSearchKey } from '../../composer/mentionSearch'
import type { ComposerData, ComposerProps, ComposerValue } from '../../composer/types'

const data: ComposerData = {
  agents: [{ name: 'Gideon' }], providers: [], discovered: {},
  models: [{ name: 'configured-model', model_name: 'Configured model', provider: 'Configured provider', description: '' }],
}
const selection: ComposerValue = { agent: 'Gideon', model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' }

beforeEach(() => writeQuery('chat:send-on-enter', true, true))
afterEach(() => vi.restoreAllMocks())

function mount(options: Partial<ComposerProps> = {}) {
  const onSend = vi.fn()
  const onStop = vi.fn()
  const onAttach = vi.fn()
  const onSelect = vi.fn()
  function Host({ config }: { config: Partial<ComposerProps> }) {
    const [value, onChange] = useState(config.value ?? 'A draft')
    return <Composer {...config} value={value} onChange={onChange} onSend={onSend} onStop={onStop}
      onAttach={onAttach} onSelect={onSelect} data={config.data ?? data} selection={config.selection ?? selection}
      controls={config.controls ?? { attach: true, model: true, slash: true, mic: !!config.onTranscribe }} />
  }
  const host = render(<Host config={options} />)
  return { ...host, onSend, onStop, onAttach, onSelect,
    configure: (config: Partial<ComposerProps>) => host.rerender(<Host config={config} />) }
}

describe('donor composer on Gideon callbacks', () => {
  it('renders real donor structure around exactly one live editor and submits its current draft', () => {
    const host = mount()
    expect(host.container.querySelector('[data-slot="composer"]')).toBeInTheDocument()
    expect(host.container.querySelector('[data-slot="composer-bar"]')).toBeInTheDocument()
    expect(host.container.querySelector('[data-slot="composer-toolbar"]')).toBeInTheDocument()
    expect(host.container.querySelector('[data-slot="composer-actions"]')).toBeInTheDocument()
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    expect(screen.getAllByRole('textbox', { name: 'Message input' })).toHaveLength(1)
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: 'Current draft' } }))
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(host.onSend).toHaveBeenCalledTimes(1)
    expect(view.state.doc.toString()).toBe('Current draft')
    expect(screen.getByRole('button', { name: 'Sent' })).toBeDisabled()
  })

  it('uses the donor action for stop and queued steering without a second send', () => {
    const host = mount({ streaming: true })
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(host.onSend).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(host.onStop).toHaveBeenCalledTimes(1)
    host.configure({ streaming: true, canQueue: true })
    fireEvent.click(screen.getByRole('button', { name: 'Steer — send into the running turn' }))
    expect(host.onSend).toHaveBeenCalledTimes(1)
    expect(host.onStop).toHaveBeenCalledTimes(1)
  })

  it('sends original file objects through the existing upload callback', () => {
    const host = mount()
    const file = new File(['body'], 'notes.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByLabelText('Attach message files'), { target: { files: [file] } })
    expect(host.onAttach).toHaveBeenCalledWith([file])
    expect(host.onAttach.mock.calls[0][0][0]).toBe(file)
  })

  it('keeps configured model choices bound to the selected agent', () => {
    const host = mount()
    fireEvent.click(screen.getByRole('button', { name: 'Model: Auto' }))
    fireEvent.click(screen.getByRole('button', { name: /Configured model/ }))
    expect(host.onSelect).toHaveBeenCalledWith({ model: 'configured-model' })
  })

  it('keeps unknown context distinct from a measured zero and reports runtime defaults', () => {
    const external: ComposerData = { ...data, discovered: { code: [{ id: 'code-1', name: 'Code agent',
      runtime: 'code', description: '', provider_agent: 'code', reasoning_effort: '', models: [] }] } }
    const host = mount({ data: external, selection: { ...selection, agent: 'Code agent' }, contextPct: 0 })
    expect(screen.getByRole('button', { name: 'Context: 0% used' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Model: Auto' }))
    expect(screen.getByText('This runtime uses its own default model.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Auto.*Use-case chain/ }))
    expect(host.onSelect).toHaveBeenCalledWith({ model: 'Auto' })
  })

  it('preserves CodeMirror slash actions and never submits while the menu owns Enter', () => {
    const host = mount({ value: '/' })
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    act(() => EditorView.findFromDOM(editor)!.dispatch({ selection: { anchor: 1 } }))
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(host.onSend).not.toHaveBeenCalled()
  })

  it('keeps Enter as a newline when send-on-enter is disabled and retains Ctrl-Enter optimization', () => {
    writeQuery('chat:send-on-enter', false, true)
    const onOptimize = vi.fn()
    const host = mount({ value: 'Draft', onOptimize, controls: { optimize: true } })
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ selection: { anchor: view.state.doc.length } }))
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(view.state.doc.toString()).toBe('Draft\n')
    expect(host.onSend).not.toHaveBeenCalled()
    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true })
    expect(onOptimize).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(editor, { key: 'Enter', shiftKey: true })
    expect(view.state.doc.toString()).toBe('Draft\n\n')
    act(() => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: '' } }))
    fireEvent.keyDown(editor, { key: 'Enter', ctrlKey: true })
    expect(onOptimize).toHaveBeenCalledTimes(1)
    expect(screen.getAllByRole('textbox', { name: 'Message input' })).toHaveLength(1)
  })

  it('renders a donor command item from the live command API and inserts its actual command', async () => {
    vi.spyOn(api, 'slashCommands').mockResolvedValue([{ name: '/help', description: 'Show help' }])
    const host = mount({ value: '/' })
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ selection: { anchor: 1 } }))
    const option = await screen.findByRole('option', { name: /help.*Show help/ })
    expect(option).toHaveAttribute('data-slot', 'composer-menu-item')
    fireEvent.mouseDown(option)
    expect(view.state.doc.toString()).toBe('/help ')
    expect(host.onSend).not.toHaveBeenCalled()
  })

  it('renders a donor mention item with real file metadata and selects its path', async () => {
    await mentionResults.load(mentionSearchKey('re', undefined, true), async () => [
      { kind: 'file', id: '/work/report.txt', name: 'report.txt', sub: '/work/report.txt', size: 1024 },
    ])
    const onMentionFile = vi.fn()
    mount({ value: '@re', onMentionFile })
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ selection: { anchor: 3 } }))
    const option = await screen.findByRole('option', { name: /report.txt.*file/ })
    expect(option).toHaveAttribute('data-slot', 'composer-menu-item')
    expect(option).toHaveTextContent('/work/report.txt')
    expect(option).toHaveTextContent('1KB')
    fireEvent.mouseDown(option)
    await waitFor(() => expect(onMentionFile).toHaveBeenCalledExactlyOnceWith({ path: '/work/report.txt', name: 'report.txt' }))
    expect(view.state.doc.toString()).toBe('@report.txt ')
  })

  it('binds the donor voice control to Gideon transcription availability', () => {
    const host = mount()
    expect(host.container.querySelector('[data-slot="composer-voice-button"]')).toBeNull()
    host.configure({ onTranscribe: async () => 'spoken words' })
    expect(screen.getByRole('button', { name: 'Voice input' })).toHaveAttribute('data-slot', 'composer-voice-button')
  })
})
