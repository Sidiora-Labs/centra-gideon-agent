import { render, screen, act } from '@testing-library/react'
import { AssistantRuntimeProvider, MessagePrimitive, ThreadPrimitive, useExternalStoreRuntime, type ThreadMessageLike } from '@assistant-ui/react'
import { useState, type ReactNode } from 'react'
import { describe, expect, it } from 'vitest'
import { Thread, ThreadMessages, ThreadTranscript, useMessage } from '../../../vendor/assistant-ui/elements/thread.aui'

type StoredMessage = { id: string; role: 'user' | 'assistant'; text: string }
const initial: StoredMessage[] = [
  { id: 'u1', role: 'user', text: 'First request' },
  { id: 'a1', role: 'assistant', text: 'First answer' },
]
let addMessage: (message: StoredMessage) => void

function Runtime({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState(initial)
  addMessage = message => setMessages(current => [...current, message])
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (message: StoredMessage): ThreadMessageLike => ({
      id: message.id,
      role: message.role,
      content: [{ type: 'text', text: message.text }],
    }),
    onNew: async content => {
      const text = content.content.map(part => part.type === 'text' ? part.text : '').join('')
      setMessages(current => [...current, { id: `new-${current.length}`, role: 'user', text }])
    },
  })
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}

function ScopedUser() {
  const id = useMessage(message => message.id)
  return <MessagePrimitive.Root data-testid={`user-${id}`}><MessagePrimitive.Parts /></MessagePrimitive.Root>
}

function ScopedAssistant() {
  const id = useMessage(message => message.id)
  return <MessagePrimitive.Root data-testid={`assistant-${id}`}><MessagePrimitive.Parts /></MessagePrimitive.Root>
}

const components = { UserMessage: ScopedUser, AssistantMessage: ScopedAssistant }

describe('source-derived connected transcript', () => {
  it('runs both message overrides in the AUI message scope', () => {
    const { container } = render(<Runtime><ThreadTranscript components={components} /></Runtime>)
    expect(screen.getByTestId('user-u1')).toHaveTextContent('First request')
    expect(screen.getByTestId('assistant-a1')).toHaveTextContent('First answer')
    expect(container.querySelectorAll('[data-slot="aui_thread-viewport"]')).toHaveLength(1)
    expect(container.querySelector('[data-slot="composer"]')).toBeNull()
  })

  it('preserves caller content around live messages and forwards viewport ref', () => {
    let viewport: HTMLDivElement | null = null
    render(<Runtime><ThreadTranscript components={components} viewportRef={node => { viewport = node }} beforeMessages={<span>Before</span>} afterMessages={<span>After</span>} afterViewport={<aside data-testid="conversation-map">Map rail</aside>} /></Runtime>)
    expect(viewport).toHaveAttribute('data-slot', 'aui_thread-viewport')
    expect(screen.getByText('Before')).toBeInTheDocument()
    expect(screen.getByText('After')).toBeInTheDocument()
    expect(screen.getByTestId('conversation-map').previousElementSibling).toBe(viewport)
    act(() => addMessage({ id: 'a2', role: 'assistant', text: 'Live answer' }))
    expect(screen.getByTestId('assistant-a2')).toHaveTextContent('Live answer')
  })

  it('preserves full donor Thread with message override slots', () => {
    const { container } = render(<Runtime><Thread components={components} autoFocus={false} /></Runtime>)
    expect(screen.getByTestId('user-u1')).toHaveTextContent('First request')
    expect(screen.getByTestId('assistant-a1')).toHaveTextContent('First answer')
    expect(container.querySelectorAll('[data-slot="aui_thread-viewport"]')).toHaveLength(1)
  })

  it('allows Gideon to own root and viewport while rendering one message collection', () => {
    const { container } = render(<Runtime><ThreadPrimitive.Root><ThreadPrimitive.Viewport><ThreadMessages components={components} /></ThreadPrimitive.Viewport></ThreadPrimitive.Root></Runtime>)
    expect(screen.getByTestId('user-u1')).toBeInTheDocument()
    expect(screen.getByTestId('assistant-a1')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-slot="aui_message-group"]')).toHaveLength(0)
    expect(container.querySelectorAll('[data-slot="composer"]')).toHaveLength(0)
  })
})
