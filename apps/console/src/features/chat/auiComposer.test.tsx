import { useState } from 'react'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { EditorView } from '@codemirror/view'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ComposerStage } from '../../shared/ui/ComposerStage'
import { GideonChatRuntimeProvider } from './auiRuntime'
import { api } from '../../shared/data/api'
import { writeQuery } from '../../shared/data/data'
import type { ComposerValue } from '../../shared/ui/composer/types'

beforeEach(() => {
  localStorage.clear()
  writeQuery('chat:send-on-enter', true, true)
})

describe('connected Gideon composer stage', () => {
  it('carries donor command, attachment, model and send actions through one controlled draft', async () => {
    vi.spyOn(api, 'slashCommands').mockResolvedValue([{ name: '/help', description: 'Show help' }])
    const onRemoveAttachment = vi.fn()
    const sent: string[] = []
    const selected: string[] = []
    function Journey() {
      const [value, onChange] = useState('/')
      const [selection, setSelection] = useState<ComposerValue>({ agent: 'Gideon', model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' })
      const [attachments, setAttachments] = useState([{ id: '/work/report.txt', name: 'report.txt', state: 'done' as const }])
      return <GideonChatRuntimeProvider sessionId="session-7" turns={[]} streaming={false}
        onSend={async () => {}} onStop={async () => {}} onEdit={async () => {}} onReload={async () => {}}>
        <ComposerStage value={value} onChange={onChange} onSend={() => sent.push(value)}
          controls={{ slash: true, model: true, attach: true }} selection={selection}
          data={{ agents: [{ name: 'Gideon' }], providers: [], discovered: {}, models: [{ name: 'configured-model', model_name: 'Configured model', provider: 'Configured provider', description: '' }] }}
          onSelect={patch => { if (patch.model) selected.push(patch.model); setSelection(previous => ({ ...previous, ...patch })) }}
          attachments={attachments} onRemoveAttachment={path => { onRemoveAttachment(path); setAttachments(previous => previous.filter(item => item.id !== path)) }}
          draftKey="session-7" auiModelSelector />
      </GideonChatRuntimeProvider>
    }
    const host = render(<Journey />)
    expect(host.container.querySelector('[data-gideon-composer-stage]')).toBeInTheDocument()
    const editor = screen.getByLabelText('Message input')
    expect(screen.getAllByLabelText('Message input')).toHaveLength(1)
    const view = EditorView.findFromDOM(editor)!
    act(() => view.dispatch({ selection: { anchor: 1 } }))
    fireEvent.click(await screen.findByRole('option', { name: /help.*Show help/ }))
    await waitFor(() => expect(view.state.doc.toString()).toBe('/help '))
    expect(JSON.parse(localStorage.getItem('gideon:composer-draft:session-7')!).draft).toBe('/help ')
    fireEvent.click(screen.getByRole('button', { name: 'Remove report.txt' }))
    expect(onRemoveAttachment).toHaveBeenCalledExactlyOnceWith('/work/report.txt')
    expect(screen.queryByText('report.txt')).not.toBeInTheDocument()
    fireEvent.change(host.container.querySelector('[data-slot="model-selector-trigger"]') as HTMLElement,
      { target: { value: 'configured-model' } })
    expect(selected).toEqual(['configured-model'])
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(sent).toEqual(['/help '])
  })
})
