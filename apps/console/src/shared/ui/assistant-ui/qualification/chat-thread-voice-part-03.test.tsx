import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { AssistantModal } from '../../../vendor/assistant-ui/elements/assistant-modal.aui'
import { AssistantSidebar } from '../../../vendor/assistant-ui/elements/assistant-sidebar.aui'
import { ThreadListSidebar } from '../../../vendor/assistant-ui/elements/thread-list.aui'
import { ThreadList } from '../../../vendor/assistant-ui/elements/thread-list.aui'
import { ThreadTranscript } from '../../../vendor/assistant-ui/elements/thread.aui'
import { ConversationMapAui } from '../../../vendor/assistant-ui/elements/conversation-map.aui'
import { VoiceOrb } from '../../../vendor/assistant-ui/elements/voice.aui'
import { GideonChatRuntimeProvider } from '../../../../features/chat/auiRuntime'
import { ThreadChatPreview, ThreadSessionSearch } from '../../../../features/chat/auiThreadSurfaces'
import type { ChatTurn } from '../../../../features/chat/chatTypes'
import type { ChatSessionSummary } from '../../../data/api'

afterEach(cleanup)

const previewTurns: ChatTurn[] = [
  { role: 'user', segments: [{ kind: 'text', text: 'Review this file' }] },
  { role: 'assistant', segments: [{ kind: 'text', text: 'The file needs one edit' }] },
]
const ownedSessions: ChatSessionSummary[] = [
  { key: 'one', title: 'File review', messages: 2, last_message: 'The file needs one edit' },
  { key: 'two', title: 'Second review', messages: 3, last_message: 'Another result' },
]

describe('connected conversation preview layouts', () => {
  it('keeps the existing session preview reachable beside history on desktop', () => {
    const open = vi.fn()
    const close = vi.fn()
    const { container } = render(<AssistantSidebar thread={<div>
      <button type="button" onClick={close}>Close chat preview</button>
      <ThreadChatPreview turns={previewTurns}/>
      <button type="button" onClick={open}>Continue in full chat</button>
    </div>}>
      <ThreadSessionSearch sessions={ownedSessions} activeId="one" onSelect={open}/>
    </AssistantSidebar>)
    expect(container.querySelectorAll('[data-slot="aui_assistant-sidebar"]')).toHaveLength(1)
    expect(screen.getAllByText('The file needs one edit')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Continue in full chat' }))
    expect(open).toHaveBeenCalledOnce()
    fireEvent.click(screen.getByRole('button', { name: 'Close chat preview' }))
    expect(close).toHaveBeenCalledOnce()
  })

  it('opens one mobile preview and switches to real session search without a second composer', () => {
    const change = vi.fn()
    const select = vi.fn()
    const { rerender } = render(<AssistantModal open trigger={null} onOpenChange={change}
      thread={<ThreadChatPreview turns={previewTurns}/>}
      history={<ThreadSessionSearch sessions={ownedSessions} activeId="one" onSelect={select}/>}/>)
    expect(screen.getByRole('dialog', { name: 'Assistant' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Open assistant' })).toBeNull()
    expect(screen.getAllByText('Review this file')).toHaveLength(1)
    expect(screen.queryByRole('textbox', { name: 'Quick reply' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'History' }))
    expect(screen.getByText('File review')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Second review/ }))
    expect(select).toHaveBeenCalledWith('two')
    fireEvent.click(screen.getByRole('button', { name: 'Close assistant' }))
    expect(change).toHaveBeenCalledWith(false)
    rerender(<AssistantModal open={false} trigger={null} thread={<ThreadChatPreview turns={previewTurns}/>} history={<ThreadSessionSearch sessions={ownedSessions} activeId="one" onSelect={select}/>}/>)
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

describe('owner-branded thread list sidebar', () => {
  it('uses the actual chat navigation and search surface without donor branding', () => {
    const history = vi.fn()
    const select = vi.fn()
    render(<ThreadListSidebar header={<span>Gideon conversations</span>}
      footer={<button type="button" onClick={history}>View all chats</button>}>
      <ThreadSessionSearch sessions={ownedSessions} activeId="one" onSelect={select}/>
    </ThreadListSidebar>)
    expect(screen.getByText('Gideon conversations')).toBeTruthy()
    expect(screen.queryByText('assistant-ui')).toBeNull()
    expect(screen.queryByText('GitHub · View Source')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Second review/ }))
    expect(select).toHaveBeenCalledWith('two')
    fireEvent.click(screen.getByRole('button', { name: 'View all chats' }))
    expect(history).toHaveBeenCalledOnce()
  })
})

describe('live assistant-ui thread navigation', () => {
  it('switches and creates sessions through the existing Gideon navigation adapter', () => {
    const switchSession = vi.fn()
    const newSession = vi.fn()
    render(<GideonChatRuntimeProvider sessionId="one" turns={previewTurns} streaming={false} sessions={ownedSessions}
      onSwitchSession={switchSession} onNewSession={newSession} onSend={() => {}} onStop={() => {}} onEdit={() => {}} onReload={() => {}}>
      <ThreadList/>
    </GideonChatRuntimeProvider>)
    expect(screen.getByText('File review')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Second review' }))
    expect(switchSession).toHaveBeenCalledWith('two')
    fireEvent.click(screen.getByRole('button', { name: 'New Thread' }))
    expect(newSession).toHaveBeenCalledOnce()
  })

  it('maps the same real runtime turns into the donor conversation rail', () => {
    class Observer { observe() {} disconnect() {} }
    vi.stubGlobal('ResizeObserver', Observer)
    try {
      render(<GideonChatRuntimeProvider sessionId="one" turns={previewTurns} streaming={false}
        onSend={() => {}} onStop={() => {}} onEdit={() => {}} onReload={() => {}}>
        <ThreadTranscript beforeMessages={<ConversationMapAui side="right"/>}/>
      </GideonChatRuntimeProvider>)
      expect(screen.getByRole('navigation', { name: 'Conversation map' })).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Review this file' })).toBeTruthy()
      expect(screen.getByText('The file needs one edit')).toBeTruthy()
    } finally { vi.unstubAllGlobals() }
  })

  it('shows a provider-backed idle voice entry without claiming an active call', () => {
    const { container } = render(<GideonChatRuntimeProvider sessionId="one" turns={[]} streaming={false}
      onSend={() => {}} onStop={() => {}} onEdit={() => {}} onReload={() => {}}>
      <VoiceOrb state="idle"/>
    </GideonChatRuntimeProvider>)
    expect(container.querySelector('canvas.aui-voice-orb[data-state="idle"]')).toBeTruthy()
    expect(container.querySelector('canvas[data-state="speaking"]')).toBeNull()
  })
})
