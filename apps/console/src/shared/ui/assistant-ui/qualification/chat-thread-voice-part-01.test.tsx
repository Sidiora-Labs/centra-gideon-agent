import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { ThreadChatPreview, ThreadConnectionNotice, ThreadConversationSearch, ThreadEmptyWelcome, ThreadSessionSearch, recentCanvasTurns } from '../../../../features/chat/auiThreadSurfaces'
import type { ChatTurn } from '../../../../features/chat/chatTypes'
import type { ChatSessionSummary } from '../../../data/api'
import { ScrollAnchor } from '../../../vendor/assistant-ui/elements/scroll-anchor'
import { VoiceConversation } from '../../../vendor/assistant-ui/elements/voice-conversation'

afterEach(cleanup)

const turns: ChatTurn[] = [
  { role: 'user', ts: '2026-09-26T10:00:00Z', segments: [{ kind: 'text', text: 'Find the blue report' }] },
  { role: 'assistant', ts: '2026-09-26T10:00:05Z', segments: [{ kind: 'text', text: 'Blue report is in the folder' }] },
  { role: 'user', ts: '2026-09-26T10:01:00Z', segments: [{ kind: 'text', text: 'Open the report' }] },
]

describe('conversation search over real turn segments', () => {
  it('shows the matching transcript content and advances to the selected turn', () => {
    const first = document.createElement('div')
    const second = document.createElement('div')
    const firstScroll = vi.fn()
    const secondScroll = vi.fn()
    first.scrollIntoView = firstScroll
    second.scrollIntoView = secondScroll
    render(<ThreadConversationSearch turns={turns} nodeOf={(index) => index === 0 ? first : index === 1 ? second : null} onClose={() => {}}/>)
    fireEvent.change(screen.getByRole('textbox', { name: 'Find in conversation' }), { target: { value: 'blue' } })
    expect(screen.getByText('1/2')).toBeTruthy()
    expect(screen.getByText('blue')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Next match' }))
    expect(secondScroll).toHaveBeenCalledWith({ behavior: 'smooth', block: 'center' })
    expect(firstScroll).not.toHaveBeenCalled()
    expect(screen.getByText('2/2')).toBeTruthy()
  })

  it('supports Unicode case folding and backward keyboard navigation', () => {
    const multilingual: ChatTurn[] = [
      { role: 'user', segments: [{ kind: 'text', text: 'İSTANBUL' }] },
      { role: 'assistant', segments: [{ kind: 'text', text: 'istanbul' }] },
    ]
    const atFirst = document.createElement('div')
    atFirst.scrollIntoView = vi.fn()
    render(<ThreadConversationSearch turns={multilingual} nodeOf={() => atFirst} onClose={() => {}}/>)
    const input = screen.getByRole('textbox', { name: 'Find in conversation' })
    fireEvent.change(input, { target: { value: 'istanbul' } })
    expect(screen.getByText('1/1')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(atFirst.scrollIntoView).toHaveBeenCalledOnce()
  })

  it('clears stale results when the query changes and closes with Escape', () => {
    const close = vi.fn()
    render(<ThreadConversationSearch turns={turns} nodeOf={() => null} onClose={close}/>)
    const input = screen.getByRole('textbox', { name: 'Find in conversation' })
    fireEvent.change(input, { target: { value: 'report' } })
    expect(screen.getByText('1/3')).toBeTruthy()
    fireEvent.change(input, { target: { value: 'absent' } })
    expect(screen.getByText('0')).toBeTruthy()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(close).toHaveBeenCalledOnce()
  })
})

const sessions: ChatSessionSummary[] = [
  { key: 'pinned', title: 'Pinned report', messages: 2, pinned: true, last_message: 'Blue report' },
  { key: 'active', title: 'Current task', messages: 4, prompt_preview: 'Open the folder' },
  { key: 'archived', title: 'Old task', messages: 6, lifecycle: 'archived', last_message: 'Finished' },
  { key: 'automation', title: 'Automatic run', messages: 1, origin: 'loop' },
]

describe('session search over API session summaries', () => {
  it('keeps manual pinned, active and archived sessions and omits automation', () => {
    render(<ThreadSessionSearch sessions={sessions} activeId="active" onSelect={() => {}}/>)
    expect(screen.getByText('Pinned report')).toBeTruthy()
    expect(screen.getByText('Current task')).toBeTruthy()
    expect(screen.getByText('Old task')).toBeTruthy()
    expect(screen.queryByText('Automatic run')).toBeNull()
    expect(screen.getByText('Blue report')).toBeTruthy()
    expect(screen.getByText('Open the folder')).toBeTruthy()
  })

  it('filters by actual preview and delegates session navigation', () => {
    const select = vi.fn()
    render(<ThreadSessionSearch sessions={sessions} activeId="active" onSelect={select}/>)
    fireEvent.change(screen.getByRole('textbox', { name: 'Search threads' }), { target: { value: 'Finished' } })
    expect(screen.queryByText('Pinned report')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Old task/ }))
    expect(select).toHaveBeenCalledWith('archived')
  })

  it('lets keyboard navigation select a filtered session', () => {
    const select = vi.fn()
    render(<ThreadSessionSearch sessions={sessions} activeId="active" onSelect={select}/>)
    const input = screen.getByRole('textbox', { name: 'Search threads' })
    fireEvent.change(input, { target: { value: 'Pinned' } })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(select).toHaveBeenCalledWith('pinned')
  })
})

describe('connected thread presentational surfaces', () => {
  it('renders a real turn preview with user and assistant roles', () => {
    const { container } = render(<ThreadChatPreview turns={turns}/>)
    const panel = container.querySelector('[data-slot="chat-panel"]')
    expect(panel).toBeTruthy()
    expect(within(panel as HTMLElement).getByText('Find the blue report')).toBeTruthy()
    expect(container.querySelector('[data-slot="chat-panel-assistant-message"]')?.textContent).toBe('Blue report is in the folder')
  })

  it('shows live preview output without dropping its saved conversation or scroll target', () => {
    const end = vi.fn()
    const { container, rerender } = render(<ThreadChatPreview turns={turns} streamingText="Checking the report" renderAssistant={(text) => <strong>{text}</strong>} endRef={end}/>)
    expect(screen.getByText('Blue report is in the folder').tagName).toBe('STRONG')
    expect(screen.getByText('Checking the report').tagName).toBe('STRONG')
    expect(end).toHaveBeenCalledWith(expect.any(HTMLDivElement))
    rerender(<ThreadChatPreview turns={turns} busy/>)
    expect(screen.queryByText('Checking the report')).toBeNull()
    expect(screen.getByText('Blue report is in the folder')).toBeTruthy()
    expect(container.querySelector('[data-slot="chat-panel-typing"]')).toBeTruthy()
  })

  it('keeps empty-state suggestions connected to the Gideon draft callback', () => {
    const pick = vi.fn()
    render(<ThreadEmptyWelcome greeting="Good afternoon" suggestions={['Write a plan', 'Summarize this']} onPick={pick}/>)
    expect(screen.getByRole('heading', { name: 'Good afternoon' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Write a plan' }))
    expect(pick).toHaveBeenCalledWith('Write a plan')
  })

  it('shows the connection notice only for the actual disconnected state', () => {
    const { rerender, container } = render(<ThreadConnectionNotice connected/>)
    expect(container.querySelector('[data-slot="connection-state"]')).toBeNull()
    rerender(<ThreadConnectionNotice connected={false}/>)
    expect(screen.getByRole('status').textContent).toContain('Reconnecting')
    rerender(<ThreadConnectionNotice connected/>)
    expect(container.querySelector('[data-slot="connection-state"]')).toBeNull()
  })
})

describe('controlled transcript scroll anchor', () => {
  it('uses the real viewport state without presenting demo messages or inventing unread turns', () => {
    const viewport = document.createElement('div')
    const jump = vi.fn()
    const { rerender, container } = render(<ScrollAnchor viewportRef={{ current: viewport }} pinned={false} showJump unreadCount={0} onJump={jump}/>)
    expect(screen.getByRole('button', { name: 'Jump to latest' })).toBeTruthy()
    expect(container.querySelector('[data-slot="scroll-anchor"] > div')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Jump to latest' }))
    expect(jump).toHaveBeenCalledOnce()
    rerender(<ScrollAnchor viewportRef={{ current: viewport }} pinned={false} showJump unreadCount={2} onJump={jump}/>)
    expect(screen.getByRole('button', { name: '2 new messages' })).toBeTruthy()
    rerender(<ScrollAnchor viewportRef={{ current: viewport }} pinned showJump={false} unreadCount={2} onJump={jump}/>)
    expect(screen.queryByRole('button', { name: '2 new messages' })).toBeNull()
  })
})

describe('editable canvas conversation context', () => {
  it('takes recent real text turns, keeps roles, and omits empty tool-only turns', () => {
    const context: ChatTurn[] = [
      { role: 'user', segments: [{ kind: 'text', text: 'first' }] },
      { role: 'assistant', segments: [{ kind: 'text', text: 'second' }] },
      { role: 'assistant', segments: [{ kind: 'tool', id: 't1', tool: 'read_file', done: true, ok: true }] },
      { role: 'user', segments: [{ kind: 'text', text: 'third' }] },
      { role: 'assistant', segments: [{ kind: 'text', text: 'fourth' }] },
      { role: 'user', segments: [{ kind: 'text', text: 'fifth' }] },
    ]
    expect(recentCanvasTurns(context)).toEqual([
      { speaker: 'assistant', text: 'second' },
      { speaker: 'user', text: 'third' },
      { speaker: 'assistant', text: 'fourth' },
      { speaker: 'user', text: 'fifth' },
    ])
  })

  it('bounds long preview text without changing the editable file content', () => {
    const longText = 'x'.repeat(800)
    expect(recentCanvasTurns([{ role: 'assistant', segments: [{ kind: 'text', text: longText }] }])).toEqual([
      { speaker: 'assistant', text: longText.slice(0, 500) },
    ])
  })
})

describe('live voice conversation controls', () => {
  it('shows only actual websocket captions and delegates microphone and end actions', () => {
    const mute = vi.fn()
    const end = vi.fn()
    const { rerender } = render(<VoiceConversation mode="listening" amplitude={0} muted={false}
      transcript={[{ id: 'user', role: 'user', text: 'Can you hear me?' }]} onToggleMute={mute} onEnd={end}/>)
    expect(screen.getByText('Can you hear me?')).toBeTruthy()
    expect(screen.queryByText('Hello there')).toBeNull()
    expect(screen.getByText('Listening')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Turn the microphone off' }))
    expect(mute).toHaveBeenCalledOnce()
    rerender(<VoiceConversation mode="speaking" amplitude={0} muted
      transcript={[{ id: 'assistant', role: 'assistant', text: 'I can hear you.' }]} onToggleMute={mute} onEnd={end}/>)
    expect(screen.getByText('I can hear you.')).toBeTruthy()
    expect(screen.getByText('Mic off')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'End the call' }))
    expect(end).toHaveBeenCalledOnce()
  })

  it('leaves interrupt unavailable when the hosted call has no interrupt callback', () => {
    render(<VoiceConversation mode="speaking" amplitude={0} transcript={[]} onToggleMute={() => {}} onEnd={() => {}}/>)
    expect(screen.getByRole('button', { name: 'Interrupt the assistant' }).hasAttribute('disabled')).toBe(true)
  })
})
