import { useState, type ReactNode } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Composer } from '../../Composer'
import { GideonChatRuntimeProvider } from '../../../../features/chat/auiRuntime'
import { writeQuery } from '../../../data/data'
import type { ComposerData, ComposerValue } from '../../composer/types'
import { api } from '../../../data/api'
import { mentionResults, mentionSearchKey } from '../../composer/mentionSearch'

const data: ComposerData = {
  agents: [{ name: 'Gideon' }], providers: [], discovered: {},
  models: [{ name: 'configured-model', model_name: 'Configured model', provider: 'Configured provider', description: '' }],
}
const selection: ComposerValue = { agent: 'Gideon', model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' }

beforeEach(() => writeQuery('chat:send-on-enter', true, true))

function Runtime({ children }: { children: ReactNode }) {
  return <GideonChatRuntimeProvider sessionId={null} turns={[]} streaming={false}
    onSend={async () => {}} onStop={async () => {}} onEdit={async () => {}} onReload={async () => {}}>
    {children}
  </GideonChatRuntimeProvider>
}

describe('AUI connected model selector', () => {
  it('hides model pickers under hosted policy while retaining donor slash actions', async () => {
    vi.spyOn(api, 'slashCommands').mockResolvedValue([{ name: '/help', description: 'Show help' }])
    const host = render(<Runtime><Composer value="/" onChange={vi.fn()} onSend={vi.fn()}
      controls={{ model: true, slash: true }} data={data} selection={selection} auiModelSelector={false} /></Runtime>)
    expect(host.container.querySelector('[data-slot="model-selector-trigger"]')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Model: Auto' })).toBeNull()
    const editor = screen.getByLabelText('Message input')
    act(() => EditorView.findFromDOM(editor)!.dispatch({ selection: { anchor: 1 } }))
    expect(await screen.findByRole('option', { name: /help.*Show help/ })).toBeInTheDocument()
    expect(host.container.querySelector('[data-slot="composer-trigger-popover"]')).toBeInTheDocument()
  })

  it('uses the runtime scoped donor selector and delegates selection to Gideon exactly once', async () => {
    const onSelect = vi.fn()
    const host = render(<Runtime><Composer value="Draft" onChange={vi.fn()} onSend={vi.fn()}
      controls={{ model: true }} data={data} selection={selection} onSelect={onSelect} auiModelSelector /></Runtime>)
    const trigger = host.container.querySelector('[data-slot="model-selector-trigger"]') as HTMLElement
    expect(trigger).toBeInTheDocument()
    expect(trigger).toHaveTextContent('Auto')
    fireEvent.change(trigger, { target: { value: 'configured-model' } })
    expect(onSelect).toHaveBeenCalledExactlyOnceWith({ model: 'configured-model' })
    host.rerender(<Runtime><Composer value="Draft" onChange={vi.fn()} onSend={vi.fn()}
      controls={{ model: true }} data={data} selection={{ ...selection, model: 'configured-model' }}
      onSelect={onSelect} auiModelSelector /></Runtime>)
    expect(trigger).toHaveTextContent('Configured model')
  })

  it('keeps operator Auto in the controlled list without changing Gideon selection implicitly', async () => {
    const onSelect = vi.fn()
    const host = render(<Runtime><Composer value="Draft" onChange={vi.fn()} onSend={vi.fn()}
      controls={{ model: true }} data={data} selection={{ ...selection, model: 'configured-model' }}
      onSelect={onSelect} auiModelSelector /></Runtime>)
    fireEvent.change(host.container.querySelector('[data-slot="model-selector-trigger"]') as HTMLElement,
      { target: { value: 'Auto' } })
    expect(onSelect).toHaveBeenCalledExactlyOnceWith({ model: 'Auto' })
  })
})

describe('donor reasoning effort in the live composer', () => {
  it('selects a supported effort without inventing a thinking budget or spent tokens', () => {
    const onSelect = vi.fn()
    const host = render(<Composer value="Draft" onChange={vi.fn()} onSend={vi.fn()}
      controls={{ reasoning: true }} data={data} selection={selection} onSelect={onSelect} />)
    fireEvent.click(screen.getByRole('button', { name: 'Reasoning effort: Default' }))
    const effort = document.querySelector('[data-slot="reasoning-effort"]')
    expect(effort).toBeInTheDocument()
    expect(effort).not.toHaveTextContent(/0\s*\/\s*0/)
    expect(effort?.querySelector('[role="progressbar"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'High' }))
    expect(onSelect).toHaveBeenCalledExactlyOnceWith({ reasoning: 'high' })
    host.rerender(<Composer value="Draft" onChange={vi.fn()} onSend={vi.fn()}
      controls={{ reasoning: true }} data={data} selection={{ ...selection, reasoning: 'high' }} onSelect={onSelect} />)
    fireEvent.click(screen.getByRole('button', { name: 'Reasoning effort: High' }))
    expect(screen.getByRole('button', { name: 'High' })).toHaveAttribute('aria-pressed', 'true')
  })
})

describe('AUI trigger popover over the single Gideon editor', () => {
  it('executes a real slash command with Enter without submitting or rendering the legacy menu', async () => {
    vi.spyOn(api, 'slashCommands').mockResolvedValue([{ name: '/help', description: 'Show help' }])
    const onSend = vi.fn()
    function Host() {
      const [value, setValue] = useState('/')
      return <Runtime><Composer value={value} onChange={setValue} onSend={onSend}
        controls={{ slash: true }} auiModelSelector /></Runtime>
    }
    const host = render(<Host />)
    const editor = screen.getByLabelText('Message input')
    expect(screen.getAllByLabelText('Message input')).toHaveLength(1)
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ selection: { anchor: 1 } }))
    expect(await screen.findByRole('option', { name: /help.*Show help/ })).toBeInTheDocument()
    expect(host.container.querySelector('[data-slot="composer-trigger-popover"]')).toBeInTheDocument()
    const listbox = screen.getByRole('listbox', { name: 'Slash commands' })
    await waitFor(() => expect(editor).toHaveAttribute('aria-controls', listbox.id))
    expect(editor).toHaveAttribute('role', 'combobox')
    fireEvent.keyDown(editor, { key: 'Enter' })
    await waitFor(() => expect(view.state.doc.toString()).toBe('/help '))
    expect(view.state.selection.main.head).toBe('/help '.length)
    expect(onSend).not.toHaveBeenCalled()
    expect(screen.queryByRole('listbox', { name: 'Slash commands' })).not.toBeInTheDocument()
  })

  it('uses CodeMirror caret position and donor selection to attach the original file path', async () => {
    await mentionResults.load(mentionSearchKey('re', undefined, false), async () => [
      { kind: 'file', id: '/work/report.txt', name: 'report.txt', sub: '/work/report.txt' },
    ])
    const onMentionFile = vi.fn()
    function Host() {
      const [value, setValue] = useState('Read @re today')
      return <Runtime><Composer value={value} onChange={setValue} onSend={vi.fn()}
        onMentionFile={onMentionFile} controls={{}} auiModelSelector /></Runtime>
    }
    const host = render(<Host />)
    const editor = screen.getByLabelText('Message input')
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ selection: { anchor: 8 } }))
    const option = await screen.findByRole('option', { name: /report.txt.*work\/report.txt/ })
    expect(host.container.querySelectorAll('[data-slot="composer-trigger-popover"]')).toHaveLength(1)
    fireEvent.click(option)
    await waitFor(() => expect(view.state.doc.toString()).toBe('Read @report.txt today'))
    expect(view.state.selection.main.head).toBe('Read @report.txt '.length)
    expect(onMentionFile).toHaveBeenCalledExactlyOnceWith({ path: '/work/report.txt', name: 'report.txt' })
    expect(screen.getAllByLabelText('Message input')).toHaveLength(1)
  })

  it('does not submit or execute a donor slash item during IME composition', async () => {
    vi.spyOn(api, 'slashCommands').mockResolvedValue([{ name: '/help', description: 'Show help' }])
    const onSend = vi.fn()
    function Host() {
      const [value, setValue] = useState('/')
      return <Runtime><Composer value={value} onChange={setValue} onSend={onSend}
        controls={{ slash: true }} auiModelSelector /></Runtime>
    }
    render(<Host />)
    const editor = screen.getByLabelText('Message input')
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ selection: { anchor: 1 } }))
    const option = await screen.findByRole('option', { name: /help.*Show help/ })
    fireEvent.compositionStart(editor)
    fireEvent.keyDown(editor, { key: 'Enter' })
    expect(view.state.doc.toString()).toBe('/')
    expect(onSend).not.toHaveBeenCalled()
    expect(option).toBeInTheDocument()
    fireEvent.compositionEnd(editor)
    fireEvent.click(option)
    await waitFor(() => expect(view.state.doc.toString()).toBe('/help '))
    expect(onSend).not.toHaveBeenCalled()
  })

  it('executes a knowledge mention at the real caret and forwards its stable ID once', async () => {
    await mentionResults.load(mentionSearchKey('kn', undefined, false), async () => [
      { kind: 'knowledge', id: 'knowledge-7', name: 'Notebook', sub: 'Team notes' },
    ])
    const onMentionKnowledge = vi.fn()
    function Host() {
      const [value, setValue] = useState('Ask @kn later')
      return <Runtime><Composer value={value} onChange={setValue} onSend={vi.fn()}
        onMentionKnowledge={onMentionKnowledge} controls={{}} auiModelSelector /></Runtime>
    }
    const host = render(<Host />)
    const editor = screen.getByLabelText('Message input')
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ selection: { anchor: 7 } }))
    const option = await screen.findByRole('option', { name: /Notebook.*Team notes/ })
    expect(host.container.querySelectorAll('[data-slot="composer-trigger-popover"]')).toHaveLength(1)
    fireEvent.click(option)
    await waitFor(() => expect(view.state.doc.toString()).toBe('Ask @Notebook later'))
    expect(view.state.selection.main.head).toBe('Ask @Notebook '.length)
    expect(onMentionKnowledge).toHaveBeenCalledExactlyOnceWith({ id: 'knowledge-7', name: 'Notebook' })
  })
})
