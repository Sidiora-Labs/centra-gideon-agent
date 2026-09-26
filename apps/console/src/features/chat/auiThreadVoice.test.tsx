import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MessagePrimitive } from '../../shared/vendor/assistant-ui'
import { ThreadTranscript, useMessage } from '../../shared/vendor/assistant-ui/elements/thread.aui'
import { MessageAssistant } from '../../shared/ui/chat/MessageAssistant'
import { MessageUser } from '../../shared/ui/chat/MessageUser'
import { ThreadAssistantTurnActions, ThreadErrorSegment } from '../ChatPage'
import { GideonChatRuntimeProvider, useGideonTurnByAuiId } from './auiRuntime'
import { assistantTurn, turnText, userTurn, type ChatTurn } from './chatTypes'

afterEach(cleanup)

function UserSlot() {
  const id = useMessage((message) => message.id)
  const record = useGideonTurnByAuiId().get(id)
  return <MessagePrimitive.Root data-turn-id={id}>
    {record && <MessageUser>{turnText(record.turn)}</MessageUser>}
  </MessagePrimitive.Root>
}

function AssistantSlot() {
  const id = useMessage((message) => message.id)
  const record = useGideonTurnByAuiId().get(id)
  return <MessagePrimitive.Root data-turn-id={id}>
    {record && <MessageAssistant>{turnText(record.turn)}</MessageAssistant>}
  </MessagePrimitive.Root>
}

function ConnectedThread() {
  const [turns, setTurns] = useState<ChatTurn[]>([
    userTurn('Explain the result', '2026-09-26T12:00:00Z'),
    assistantTurn('First'),
  ])
  return <GideonChatRuntimeProvider sessionId="thread-1" turns={turns} streaming={false}
    onSend={(text) => setTurns((previous) => [...previous, userTurn(text)])}
    onStop={() => {}} onEdit={() => {}} onReload={() => {}}>
    <ThreadTranscript components={{ UserMessage: UserSlot, AssistantMessage: AssistantSlot }}/>
    <button type="button" onClick={() => setTurns((previous) => [previous[0], assistantTurn('Final answer')])}>Update answer</button>
  </GideonChatRuntimeProvider>
}

describe('connected Gideon thread', () => {
  it('renders one transcript from the real turn map and updates an answer without duplicating it', () => {
    const { container } = render(<ConnectedThread/>)
    expect(screen.getByText('Explain the result')).toBeTruthy()
    expect(screen.getByText('First')).toBeTruthy()
    expect(container.querySelectorAll('[data-turn-id]')).toHaveLength(2)
    expect(container.querySelectorAll('[data-slot="aui_thread-viewport"]')).toHaveLength(1)
    const ids = Array.from(container.querySelectorAll('[data-turn-id]'), (node) => node.getAttribute('data-turn-id'))
    fireEvent.click(screen.getByRole('button', { name: 'Update answer' }))
    expect(screen.queryByText('First')).toBeNull()
    expect(screen.getByText('Final answer')).toBeTruthy()
    expect(Array.from(container.querySelectorAll('[data-turn-id]'), (node) => node.getAttribute('data-turn-id'))).toEqual(ids)
  })

  it('uses one donor action row for persisted votes and real answer navigation', () => {
    const events: string[] = []
    function Actions() {
      const [reaction, setReaction] = useState<'up' | 'down' | null>(null)
      const [index, setIndex] = useState(0)
      return <ThreadAssistantTurnActions text="Answer body" canFork variantCount={3} variantIdx={index}
        speaking={false} reaction={reaction} reactionBusy={false}
        onRegenerate={() => events.push('regenerate')} onFork={() => events.push('fork')} onSpeak={() => events.push('speak')}
        onSwitchVariant={(next) => { setIndex(next); events.push(`variant:${next}`) }}
        onFeedback={(verdict) => { setReaction(verdict); events.push(`feedback:${verdict}`) }}/>
    }
    const { container } = render(<Actions/>)
    expect(container.querySelectorAll('[data-slot="message-actions"]')).toHaveLength(1)
    expect(container.querySelectorAll('[data-slot="message-branches"]')).toHaveLength(1)
    expect(screen.queryByText('Answer body')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /next (answer|response)/i }))
    expect(events).toContain('variant:1')
    expect(screen.getByText('2 / 3')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Mark response helpful' }))
    expect(events).toContain('feedback:up')
    expect(screen.getByRole('button', { name: 'Mark response helpful' }).hasAttribute('disabled')).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Mark response helpful' }))
    expect(events.filter((value) => value === 'feedback:up')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Mark response unhelpful' }))
    expect(events).toContain('feedback:down')
    fireEvent.click(screen.getByRole('button', { name: /Regenerate/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Branch from here' }))
    fireEvent.click(screen.getByRole('button', { name: 'Speak' }))
    expect(events.slice(-3)).toEqual(['regenerate', 'fork', 'speak'])
    expect(screen.queryByRole('button', { name: 'More response actions' })).toBeNull()
  })

  it('shows a real assistant error without advertising an unavailable retry action', () => {
    const { container } = render(<ThreadErrorSegment text="Provider request failed" onSetupModel={() => {}}/>)
    expect(container.querySelector('[data-slot="error-state"]')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain('Provider request failed')
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  })
})
