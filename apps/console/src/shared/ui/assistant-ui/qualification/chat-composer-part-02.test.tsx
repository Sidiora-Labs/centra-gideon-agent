import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Composer } from '../../Composer'
import { PromptPalette } from '../../../../features/chat/PromptPalette'
import { api } from '../../../data/api'
import { writeQuery } from '../../../data/data'
import type { ComposerProps } from '../../composer/types'

beforeEach(() => {
  localStorage.clear()
  writeQuery('chat:send-on-enter', true, true)
})
afterEach(() => vi.restoreAllMocks())

function mount(options: Partial<ComposerProps> = {}) {
  const onRemoveAttachment = vi.fn()
  const onOpenAttachment = vi.fn()
  const onAttach = vi.fn()
  const onSend = vi.fn()
  function Host() {
    const [value, onChange] = useState(options.value ?? '')
    return <Composer {...options} value={value} onChange={onChange} onSend={onSend}
      onAttach={onAttach} onRemoveAttachment={onRemoveAttachment} onOpenAttachment={onOpenAttachment}
      controls={options.controls ?? { attach: true }} />
  }
  const host = render(<Host />)
  return { ...host, onRemoveAttachment, onOpenAttachment, onAttach, onSend }
}

describe('source composer detail surfaces', () => {
  it('renders donor attachment chips from real IDs and removes the requested file', () => {
    const host = mount({ attachments: [
      { id: '/work/report.txt', name: 'report.txt', meta: 'Attached', state: 'done', kind: 'text' },
      { id: '/work/image.png', name: 'image.png', meta: 'Uploading', state: 'uploading', kind: 'image', progress: 40 },
    ] })
    expect(host.container.querySelectorAll('[data-slot="composer-attachment"]')).toHaveLength(2)
    expect(screen.getByText('report.txt')).toBeInTheDocument()
    expect(host.container.querySelector('[data-state="uploading"]')).toHaveTextContent('Uploading')
    fireEvent.click(screen.getByRole('button', { name: 'Remove report.txt' }))
    expect(host.onRemoveAttachment).toHaveBeenCalledExactlyOnceWith('/work/report.txt')
    fireEvent.click(screen.getByRole('button', { name: 'Open report.txt' }))
    expect(host.onOpenAttachment).toHaveBeenCalledExactlyOnceWith('/work/report.txt')
    expect(screen.queryByRole('button', { name: 'Remove image.png' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Open image.png' })).toBeNull()
  })

  it('offers a stored draft for the active session and restores it through onChange', () => {
    localStorage.setItem('gideon:composer-draft:session-7', JSON.stringify({ draft: 'Unsent plan', savedAt: '14:30' }))
    const host = mount({ draftKey: 'session-7' })
    expect(screen.getByText('Unsent plan')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Restore' }))
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    expect(EditorView.findFromDOM(editor)!.state.doc.toString()).toBe('Unsent plan')
    expect(host.container.querySelector('[data-slot="draft-restore"]')).toBeNull()
  })

  it('persists edits and clears the saved draft after the controlled editor empties', () => {
    mount({ draftKey: 'session-8' })
    const editor = screen.getByRole('textbox', { name: 'Message input' })
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ changes: { from: 0, insert: 'A new draft' } }))
    expect(JSON.parse(localStorage.getItem('gideon:composer-draft:session-8')!).draft).toBe('A new draft')
    act(() => view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: '' } }))
    expect(localStorage.getItem('gideon:composer-draft:session-8')).toBeNull()
  })

  it('discards only the current session draft without changing the editor', () => {
    localStorage.setItem('gideon:composer-draft:session-a', JSON.stringify({ draft: 'Old A', savedAt: 'Yesterday' }))
    localStorage.setItem('gideon:composer-draft:session-b', JSON.stringify({ draft: 'Old B', savedAt: 'Yesterday' }))
    mount({ draftKey: 'session-a' })
    fireEvent.click(screen.getByRole('button', { name: 'Discard the draft' }))
    expect(localStorage.getItem('gideon:composer-draft:session-a')).toBeNull()
    expect(localStorage.getItem('gideon:composer-draft:session-b')).not.toBeNull()
    expect(EditorView.findFromDOM(screen.getByRole('textbox', { name: 'Message input' }))!.state.doc.toString()).toBe('')
  })

  it('only shows a token breakdown when the producer supplies complete exclusive buckets and a limit', () => {
    const host = mount({ contextPct: 61 })
    expect(host.container.querySelector('[data-slot="context-breakdown"]')).toBeNull()
    expect(host.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('Breakdown unavailable')
    host.unmount()
    const unknown = mount({ contextUsage: { input_tokens: 610, cache_creation_tokens: null,
      cache_read_tokens: 100, context_window_tokens: 1000 } })
    expect(unknown.container.querySelector('[data-slot="context-breakdown"]')).toBeNull()
    expect(unknown.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent(/Input\s*610 tokens/)
    expect(unknown.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent(/Cache read\s*100 tokens/)
    expect(unknown.container.querySelector('[data-slot="composer-context"]')).not.toHaveTextContent('Cache creation')
    expect(unknown.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('Breakdown incomplete')
    unknown.unmount()
    const directTotal = mount({ contextUsage: { input_tokens: 610, cache_creation_tokens: null,
      cache_read_tokens: 100, context_window_tokens: 1000, total_input_tokens: 750 } })
    expect(directTotal.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('750 / 1,000 tokens')
    expect(directTotal.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('Breakdown incomplete')
    expect(directTotal.container.querySelector('[data-slot="context-breakdown"]')).toBeNull()
    directTotal.unmount()
    const capacityOnly = mount({ contextUsage: { input_tokens: null, cache_creation_tokens: null,
      cache_read_tokens: null, context_window_tokens: 8192 } })
    expect(capacityOnly.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('Window: 8,192 tokens')
    expect(capacityOnly.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('Breakdown unavailable')
    capacityOnly.unmount()
    const totalOnly = mount({ contextUsage: { input_tokens: null, cache_creation_tokens: null,
      cache_read_tokens: null, context_window_tokens: null, total_input_tokens: 750 } })
    expect(totalOnly.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('Used: 750 tokens')
    expect(totalOnly.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('Breakdown unavailable')
    totalOnly.unmount()
    const measured = mount({ contextPct: 71, attachments: [{ id: '/work/report.txt', name: 'report.txt', state: 'done' }],
      contextUsage: { input_tokens: 610, cache_creation_tokens: 100,
        cache_read_tokens: 0, context_window_tokens: 1000 } })
    expect(measured.container.querySelector('[data-slot="context-breakdown"]')).toHaveTextContent('710 / 1,000')
    expect(measured.container.querySelector('[data-slot="context-breakdown"] [role="meter"][aria-label="Input context usage"]')).toHaveAttribute('aria-valuenow', '61')
    expect(measured.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('report.txt')
    measured.unmount()
  })

  it('shows attached context sources without inventing a zero usage percentage', () => {
    const host = mount({ attachments: [{ id: '/work/context.md', name: 'context.md', state: 'done' }] })
    expect(screen.getByRole('button', { name: 'Context: unknown' })).toBeInTheDocument()
    expect(host.container.querySelector('[data-slot="composer-context"]')).toHaveTextContent('Sources: context.md')
    expect(host.container.querySelector('[data-slot="context-breakdown"]')).toBeNull()
  })

  it('renders donor mobile structure around one CodeMirror editor with a real send action', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    const host = mount({ value: 'Mobile draft' })
    const mobileComposer = host.container.querySelector('[data-slot="mobile-composer"]')!
    expect(mobileComposer).toHaveAttribute('data-embedded', 'true')
    expect(screen.getAllByRole('textbox', { name: 'Message input' })).toHaveLength(1)
    expect(mobileComposer.querySelector('input')).toBeNull()
    expect(mobileComposer.querySelector('[aria-label="Add an attachment"]')).toBeNull()
    expect(mobileComposer.querySelector('.fade-in')).toBeNull()
    expect(mobileComposer.querySelector('span[aria-hidden].h-1')).toBeNull()
    expect(mobileComposer.querySelector('[data-slot="mobile-composer-field"]')).not.toHaveClass('rounded-[18px]', 'bg-foreground/[0.04]')
    const mobileStyle = (mobileComposer as HTMLElement).style
    expect(mobileStyle.background).toBe('transparent')
    expect(mobileStyle.borderWidth).toBe('0px')
    expect(mobileStyle.borderRadius).toBe('0px')
    expect(mobileStyle.padding).toBe('0px')
    const chooser = screen.getByLabelText('Attach message files') as HTMLInputElement
    const chooseFiles = vi.spyOn(chooser, 'click')
    fireEvent.click(screen.getByRole('button', { name: 'Attach files' }))
    expect(chooseFiles).toHaveBeenCalledOnce()
    fireEvent.change(chooser, { target: { files: [new File(['draft'], 'draft.txt', { type: 'text/plain' })] } })
    expect(host.onAttach).toHaveBeenCalledExactlyOnceWith([expect.objectContaining({ name: 'draft.txt' })])
    act(() => EditorView.findFromDOM(screen.getByRole('textbox', { name: 'Message input' }))!.focus())
    expect(screen.queryByText('return to send')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(host.onSend).toHaveBeenCalledTimes(1)
    host.unmount()
    vi.unstubAllGlobals()
  })

  it('keeps prompt and file actions in the single mobile add menu', async () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    const onOpenPrompts = vi.fn()
    const host = mount({ onOpenPrompts })
    expect(host.container.querySelector('[data-slot="mobile-composer"] [aria-label="Add an attachment"]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Add to message' }))
    fireEvent.click(screen.getByText('Saved prompts'))
    expect(onOpenPrompts).toHaveBeenCalledOnce()
    await waitFor(() => expect(screen.queryByText('Saved prompts')).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'Add to message' }))
    const chooser = screen.getByLabelText('Attach message files') as HTMLInputElement
    const chooseFiles = vi.spyOn(chooser, 'click')
    fireEvent.click(screen.getByText('Attach files'))
    expect(chooseFiles).toHaveBeenCalledOnce()
    host.unmount()
    vi.unstubAllGlobals()
  })
})

describe('real prompt library producer', () => {
  it('keeps description and tag matches from the existing prompt search when donor rows render', async () => {
    vi.spyOn(api, 'prompts').mockResolvedValue([
      { name: 'plan', title: 'Plan a change', description: 'deployment checklist', tags: ['release'], variables: [] },
      { name: 'review', title: 'Review code', description: 'inspect a diff', tags: [], variables: [] },
    ])
    render(<PromptPalette onInsert={vi.fn()} onClose={vi.fn()} />)
    await screen.findByText('Review code')
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search prompts' }), { target: { value: 'deployment' } })
    expect(screen.getByRole('option', { name: 'Plan a change' })).toBeInTheDocument()
    expect(screen.getByText('deployment checklist')).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Review code' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search prompts' }), { target: { value: 'no such prompt' } })
    expect(screen.getByText('No prompts match “no such prompt”.')).toBeInTheDocument()
    expect(screen.queryByRole('listbox', { name: 'Saved prompts' })).toBeNull()
  })

  it('loads saved prompts and renders the chosen template before inserting', async () => {
    vi.spyOn(api, 'prompts').mockResolvedValue([{ name: 'plan', title: 'Plan a change', content: 'Plan {{goal}}', variables: [] }])
    vi.spyOn(api, 'prompt').mockResolvedValue({ name: 'plan', title: 'Plan a change', content: 'Plan {{goal}}', variables: [] })
    vi.spyOn(api, 'renderPrompt').mockResolvedValue({ name: 'plan', rendered: 'Plan the deployment' })
    const onInsert = vi.fn()
    const onClose = vi.fn()
    const host = render(<PromptPalette onInsert={onInsert} onClose={onClose} />)
    await waitFor(() => expect(screen.getByText('Plan a change')).toBeInTheDocument())
    expect(document.querySelector('[data-slot="prompt-library"]')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: 'Plan a change' }))
    await waitFor(() => expect(onInsert).toHaveBeenCalledExactlyOnceWith('Plan the deployment'))
    expect(api.prompts).toHaveBeenCalledWith('user')
    expect(api.renderPrompt).toHaveBeenCalledWith('plan', {})
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('keeps the library open and reports a prompt-detail error', async () => {
    vi.spyOn(api, 'prompts').mockResolvedValue([{ name: 'plan', title: 'Plan a change', variables: [] }])
    vi.spyOn(api, 'prompt').mockRejectedValue(new Error('Prompt unavailable'))
    const onInsert = vi.fn()
    const onClose = vi.fn()
    render(<PromptPalette onInsert={onInsert} onClose={onClose} />)
    fireEvent.click(await screen.findByRole('option', { name: 'Plan a change' }))
    expect(await screen.findByText('Prompt unavailable')).toBeInTheDocument()
    expect(onInsert).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
  })
})
