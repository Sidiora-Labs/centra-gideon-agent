import { fireEvent, render, screen } from '@testing-library/react'
import { AssistantRuntimeProvider, useAui, useExternalStoreRuntime, type ThreadMessageLike } from '@assistant-ui/react'
import { useEffect, useState, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { ThreadListSidebar } from '../../../vendor/assistant-ui/elements/thread-list.aui'
import { AssistantSidebar } from '../../../vendor/assistant-ui/elements/assistant-sidebar.aui'
import { ScrollAnchor } from '../../../vendor/assistant-ui/elements/scroll-anchor'
import { ReadAloud } from '../../../vendor/assistant-ui/elements/read-aloud'
import { ModelSelector } from '../../../vendor/assistant-ui/elements/model-selector.aui'

type StoredMessage = { id: string; role: 'user' | 'assistant'; text: string }
let readModel: () => string | undefined
function Runtime({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<StoredMessage[]>([])
  const runtime = useExternalStoreRuntime({
    messages,
    convertMessage: (message: StoredMessage): ThreadMessageLike => ({ id: message.id, role: message.role, content: [{ type: 'text', text: message.text }] }),
    onNew: async message => {
      const text = message.content.map(part => part.type === 'text' ? part.text : '').join('')
      setMessages(current => [...current, { id: `new-${current.length}`, role: 'user', text }])
    },
  })
  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>
}
function ContextProbe() {
  const api = useAui()
  useEffect(() => { readModel = () => api.modelContext.getModelContext().config?.modelName }, [api])
  return null
}

describe('connected source controls', () => {
  it('uses caller header and footer instead of donor external links', () => {
    render(<Runtime><ThreadListSidebar header={<span>Gideon</span>} footer={<span>Account</span>}><div>Conversations</div></ThreadListSidebar></Runtime>)
    expect(screen.getByText('Gideon')).toBeInTheDocument()
    expect(screen.getByText('Account')).toBeInTheDocument()
    expect(screen.getByText('Conversations')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'assistant-ui' })).toBeNull()
    expect(screen.queryByRole('link', { name: /GitHub/ })).toBeNull()
  })

  it('mounts supplied thread once in the sidebar', () => {
    render(<AssistantSidebar thread={<div data-testid="gideon-thread">Connected</div>}><div>Main</div></AssistantSidebar>)
    expect(screen.getAllByTestId('gideon-thread')).toHaveLength(1)
    expect(screen.getByText('Main')).toBeInTheDocument()
  })

  it('uses external unread state and callback without demo messages', () => {
    const viewport = document.createElement('div')
    const ref = { current: viewport }
    const onJump = vi.fn()
    const { container, rerender } = render(<ScrollAnchor viewportRef={ref} pinned={false} unreadCount={2} onJump={onJump} />)
    expect(screen.getByRole('button', { name: '2 new messages' })).toBeInTheDocument()
    expect(container.querySelectorAll('[data-slot="scroll-anchor"] > div')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: '2 new messages' }))
    expect(onJump).toHaveBeenCalledTimes(1)
    rerender(<ScrollAnchor viewportRef={ref} pinned unreadCount={2} onJump={onJump} />)
    expect(screen.queryByRole('button', { name: /new messages/ })).toBeNull()
  })

  it('offers a truthful jump action while scrolled up with no unread messages', () => {
    const ref = { current: document.createElement('div') }
    const onJump = vi.fn()
    const { container } = render(<ScrollAnchor viewportRef={ref} pinned={false} unreadCount={0} showJump onJump={onJump} />)
    expect(container.querySelector('[data-slot="scroll-anchor"]')).not.toHaveClass('h-64')
    fireEvent.click(screen.getByRole('button', { name: 'Jump to latest' }))
    expect(onJump).toHaveBeenCalledTimes(1)
  })

  it('hides unknown spoken progress and timing while preserving play action', () => {
    const onToggle = vi.fn()
    render(<ReadAloud words={['Real', 'speech']} playing={false} onToggle={onToggle} />)
    expect(screen.getByText(/Real/)).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).toBeNull()
    expect(screen.queryByRole('button', { name: /Playback speed/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('keeps playback controls without duplicating caller-rendered answer text', () => {
    const onToggle = vi.fn()
    render(<ReadAloud words={['Already', 'shown']} showText={false} playing onToggle={onToggle} />)
    expect(screen.queryByText(/Already/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('announces known spoken progress and duration', () => {
    render(<ReadAloud words={['One', 'two', 'three', 'four']} spokenIndex={2} playing rate={1.25} elapsed="0:02" duration="0:04" />)
    expect(screen.getByRole('button', { name: 'Pause' })).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Read aloud progress' })).toHaveAttribute('aria-valuenow', '50')
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', '0:02 of 0:04')
    expect(screen.getByRole('button', { name: 'Playback speed, currently 1.25 times' })).toBeInTheDocument()
  })

  it('keeps an explicitly undefined controlled model out of AUI context', () => {
    const models = [{ id: 'model-1', name: 'Configured model' }]
    render(<Runtime><ContextProbe /><ModelSelector models={models} value={undefined} /></Runtime>)
    expect(readModel()).toBeUndefined()
    expect(screen.getByRole('combobox', { name: 'Model' })).toHaveValue('')
  })

  it('keeps Auto out of AUI modelContext across controlled transitions', () => {
    const models = [{ id: 'Auto', name: 'Auto' }, { id: 'model-1', name: 'Configured model' }]
    const onValueChange = vi.fn()
    const { rerender } = render(<Runtime><ContextProbe /><ModelSelector models={models} value="Auto" onValueChange={onValueChange} /></Runtime>)
    expect(readModel()).toBeUndefined()
    rerender(<Runtime><ContextProbe /><ModelSelector models={models} value="model-1" onValueChange={onValueChange} /></Runtime>)
    expect(readModel()).toBe('model-1')
    rerender(<Runtime><ContextProbe /><ModelSelector models={models} value="Auto" onValueChange={onValueChange} /></Runtime>)
    expect(readModel()).toBeUndefined()
    fireEvent.change(screen.getByRole('combobox', { name: 'Model' }), { target: { value: 'model-1' } })
    expect(onValueChange).toHaveBeenCalledWith('model-1')
  })
})
