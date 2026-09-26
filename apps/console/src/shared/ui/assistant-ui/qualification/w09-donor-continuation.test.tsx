import { fireEvent, render, screen } from '@testing-library/react'
import { AssistantRuntimeProvider, useExternalStoreRuntime, type ThreadMessageLike } from '@assistant-ui/react'
import { type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ClaudeLogo, OpenAILogo, GeminiLogo } from '../../../vendor/assistant-ui/elements/logos'
import { MessageTiming, formatTimingMs } from '../../../vendor/assistant-ui/elements/message-timing.aui'
import { ThreadTranscript } from '../../../vendor/assistant-ui/elements/thread.aui'
import { ConversationSearch } from '../../../vendor/assistant-ui/elements/conversation-search'
import { ThreadSearch } from '../../../vendor/assistant-ui/elements/thread-search'
import { ConnectionState } from '../../../vendor/assistant-ui/elements/connection-state'
import { VoiceConversation } from '../../../vendor/assistant-ui/elements/voice-conversation'
import { ChatPanel, ChatPanelMessages, ChatPanelUserMessage, ChatPanelAssistantMessage } from '../../../vendor/assistant-ui/elements/chat-panel'
import { EmptyState, EmptyStateGreeting, EmptyStateSuggestions, EmptyStateSuggestion } from '../../../vendor/assistant-ui/elements/empty-state'

function Runtime({ children, timed }: { children: ReactNode; timed: boolean }) {
  const messages = [{ id: 'a1', role: 'assistant' as const, text: 'Answer' }]
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (message: typeof messages[number]): ThreadMessageLike => ({
      id: message.id,
      role: message.role,
      content: [{ type: 'text', text: message.text }],
      metadata: timed ? { timing: { streamStartTime: 0, firstTokenTime: 120, totalStreamTime: 1250, tokensPerSecond: 42.25, totalChunks: 4, toolCallCount: 0 } } : undefined,
    }),
    onNew: async () => {},
  })
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}

describe('donor timing and logo contracts', () => {
  it('formats milliseconds and seconds without invented unknown values', () => {
    expect(formatTimingMs(undefined)).toBe('—')
    expect(formatTimingMs(12.6)).toBe('13ms')
    expect(formatTimingMs(1250)).toBe('1.25s')
  })

  it('renders all three named donor SVG logos with unique Gemini gradient', () => {
    const { container } = render(<><ClaudeLogo data-testid="claude" /><OpenAILogo data-testid="openai" /><GeminiLogo data-testid="gemini" /><GeminiLogo data-testid="gemini-two" /></>)
    expect(container.querySelectorAll('svg')).toHaveLength(4)
    expect(screen.getByTestId('claude')).toHaveAttribute('viewBox', '0 0 256 257')
    expect(screen.getByTestId('openai')).toHaveAttribute('viewBox', '0 0 256 260')
    const first = screen.getByTestId('gemini').querySelector('linearGradient')?.id
    const second = screen.getByTestId('gemini-two').querySelector('linearGradient')?.id
    expect(first).toBeTruthy()
    expect(first).not.toBe(second)
  })

  it('shows measured AUI message timing inside the message scope', () => {
    render(<Runtime timed><ThreadTranscript components={{ AssistantMessage: MessageTiming }} /></Runtime>)
    expect(screen.getByText('1.25s')).toBeInTheDocument()
    fireEvent.click(screen.getByText('1.25s'))
    expect(screen.getByText('120ms')).toBeInTheDocument()
    expect(screen.getByText('42.3 tok/s')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('hides timing when the AUI message has no measurement', () => {
    const { container } = render(<Runtime timed={false}><ThreadTranscript components={{ AssistantMessage: MessageTiming }} /></Runtime>)
    expect(container.querySelector('[data-slot="message-timing"]')).toBeNull()
  })
})

describe('donor thread and voice surfaces', () => {
  it('keeps conversation query and stepping controlled by the caller', () => {
    const onQueryChange = vi.fn()
    const onStep = vi.fn()
    render(<ConversationSearch query="find" hits={[{ id: 'h1', before: 'before', match: 'find', after: 'after', position: 4 }]} activeIndex={0} onQueryChange={onQueryChange} onStep={onStep} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Find in conversation' }), { target: { value: 'next' } })
    expect(onQueryChange).toHaveBeenCalledWith('next')
    expect(screen.getByText(/before/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next match' }))
    expect(onStep).toHaveBeenCalledWith(1)
  })

  it('filters actual thread records and selects by stable id', () => {
    const onSelect = vi.fn()
    const threads = [{ id: 't1', title: 'Budget', group: 'Today', preview: 'Costs', pinned: true }, { id: 't2', title: 'Travel', group: 'Yesterday', preview: 'Flights' }]
    render(<ThreadSearch threads={threads} query="Budget" activeId="t1" onSelect={onSelect} />)
    expect(screen.getByText('Budget')).toBeInTheDocument()
    expect(screen.queryByText('Travel')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Budget/ }))
    expect(onSelect).toHaveBeenCalledWith('t1')
  })

  it('renders connected state only when action or recovery is relevant', () => {
    const onRetry = vi.fn()
    const { container, rerender } = render(<ConnectionState phase="online" />)
    expect(container.querySelector('[data-slot="connection-state"]')).toBeNull()
    rerender(<ConnectionState phase="dropped" onRetry={onRetry} />)
    fireEvent.click(screen.getByRole('button', { name: 'Reconnect' }))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('keeps voice transcript and controls caller driven', () => {
    const onEnd = vi.fn()
    render(<VoiceConversation mode="listening" amplitude={0.4} transcript={[{ id: 'v1', role: 'user', text: 'Hello' }]} onEnd={onEnd} />)
    expect(screen.getByText('Hello')).toBeInTheDocument()
    expect(screen.getByText('Listening')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /End/ }))
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it('composes chat panel and empty state primitives with caller content', () => {
    const { container } = render(<><ChatPanel><ChatPanelMessages><ChatPanelUserMessage>Question</ChatPanelUserMessage><ChatPanelAssistantMessage>Answer</ChatPanelAssistantMessage></ChatPanelMessages></ChatPanel><EmptyState><EmptyStateGreeting>Welcome</EmptyStateGreeting><EmptyStateSuggestions><EmptyStateSuggestion>Ask</EmptyStateSuggestion></EmptyStateSuggestions></EmptyState></>)
    expect(container.querySelectorAll('[data-slot="chat-panel"]')).toHaveLength(1)
    expect(screen.getByText('Question')).toBeInTheDocument()
    expect(screen.getByText('Answer')).toBeInTheDocument()
    expect(screen.getByText('Welcome')).toBeInTheDocument()
  })
})
