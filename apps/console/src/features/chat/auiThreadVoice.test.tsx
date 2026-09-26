import { useState } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { MessagePrimitive } from '../../shared/vendor/assistant-ui'
import { ThreadTranscript, useMessage } from '../../shared/vendor/assistant-ui/elements/thread.aui'
import { MessageAssistant } from '../../shared/ui/chat/MessageAssistant'
import { MessageUser } from '../../shared/ui/chat/MessageUser'
import { applyLiveToolResult, AssistantSegments, ThreadAssistantTurnActions, ThreadErrorSegment } from '../ChatPage'
import { GideonChatRuntimeProvider, useGideonTurnByAuiId } from './auiRuntime'
import { ThreadPeekViews } from './auiThreadSurfaces'
import { assistantTurn, hydrateTurns, turnText, userTurn, type ChatTurn, type Segment } from './chatTypes'

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
    {record && <MessageAssistant stopOutcome={record.turn.stopOutcome}>{turnText(record.turn)}</MessageAssistant>}
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
  it('shows one selected-history view and returns to the live preview while a reply streams', () => {
    const history = hydrateTurns([
      { role: 'user', content: 'What changed?', ts: '2026-09-26T12:00:00Z' },
      { role: 'assistant', content: 'The report changed.', ts: '2026-09-26T12:00:05Z' },
    ])
    const view = render(<ThreadPeekViews turns={history}/> )
    expect(view.container.querySelectorAll('[data-slot="chat-panel"]')).toHaveLength(1)
    expect(view.container.querySelectorAll('[data-slot="day-separator"]')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Timeline' }))
    expect(view.container.querySelectorAll('[data-slot="chat-panel"]')).toHaveLength(0)
    expect(view.container.querySelectorAll('[data-slot="day-separator"]')).toHaveLength(1)
    expect(screen.getByText('The report changed.')).toBeTruthy()
    view.rerender(<ThreadPeekViews turns={history} streamingText="New answer underway" busy/> )
    expect(view.container.querySelectorAll('[data-slot="chat-panel"]')).toHaveLength(1)
    expect(view.container.querySelectorAll('[data-slot="day-separator"]')).toHaveLength(0)
    expect(screen.getByText('New answer underway')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Timeline' }).hasAttribute('disabled')).toBe(true)
    view.rerender(<ThreadPeekViews turns={history}/> )
    expect(view.container.querySelectorAll('[data-slot="day-separator"]')).toHaveLength(1)
    expect(view.container.querySelectorAll('[data-slot="chat-panel"]')).toHaveLength(0)
  })

  it('groups adjacent real tool calls once while preserving interleaved process order and rich results', () => {
    const segments: Segment[] = [
      { kind: 'thinking', text: 'Planning the checks' },
      { kind: 'tool', id: 'read-a', tool: 'Read', input: 'a.txt', output: 'FIRST_RESULT', purpose: 'Inspect a.txt', done: true },
      { kind: 'tool', id: 'read-b', tool: 'Read', input: 'b.txt', output: 'SECOND_RESULT', purpose: 'Inspect b.txt', done: true },
      { kind: 'activity', activityKind: 'progress', text: 'Checkpoint reached' },
      { kind: 'tool', id: 'read-c', tool: 'Read', input: 'c.txt', output: 'THIRD_RESULT', purpose: 'Inspect c.txt', done: true },
      { kind: 'text', text: 'The checks are complete.' },
    ]
    const view = render(<AssistantSegments segments={segments} isLast messageTs="2026-09-26T12:00:05Z"
      onApprove={() => {}} onSwitchToAgent={() => {}} onOpenFile={() => {}} onSetupModel={() => {}}/>)
    const groups = view.container.querySelectorAll('[data-slot="aui-tool-progress"]')
    expect(groups).toHaveLength(2)
    expect(view.container.querySelectorAll('[data-slot="tool-group-root"]')).toHaveLength(1)
    expect(view.container.querySelectorAll('[data-slot="tool-timeline"]')).toHaveLength(1)
    expect(view.container.querySelectorAll('[data-slot="tool-fallback-root"]')).toHaveLength(1)
    const checkpoint = screen.getByText('Checkpoint reached')
    expect(Boolean(groups[0].compareDocumentPosition(checkpoint) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true)
    expect(Boolean(checkpoint.compareDocumentPosition(groups[1]) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true)
    fireEvent.click(view.container.querySelector('[data-slot="tool-group-trigger"]') as HTMLButtonElement)
    expect(view.container.querySelectorAll('[data-slot="tool-fallback-root"]')).toHaveLength(3)
    const triggers = view.container.querySelectorAll('[data-slot="tool-fallback-trigger"]')
    fireEvent.click(triggers[0] as HTMLElement)
    expect(view.container.textContent?.match(/FIRST_RESULT/g)).toHaveLength(1)
    expect(screen.getByText('The checks are complete.')).toBeTruthy()
    view.rerender(<AssistantSegments segments={segments} isLast streaming messageTs="2026-09-26T12:00:05Z"
      onApprove={() => {}} onSwitchToAgent={() => {}} onOpenFile={() => {}} onSetupModel={() => {}}/>)
    expect(view.container.querySelectorAll('[data-slot="tool-group-root"]')).toHaveLength(0)
    expect(view.container.querySelectorAll('[data-slot="tool-timeline"]')).toHaveLength(2)
    expect(view.container.querySelectorAll('[data-slot="aui-tool-progress"]')).toHaveLength(2)
  })

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

  it('shows only a recorded stop outcome beside the partial answer', () => {
    const event = { kind: 'stop_event', id: 'stop-1', state: 'stopped', outcome: 'soft' }
    const turns = hydrateTurns([
      { role: 'user', content: 'Continue the analysis' },
      { role: 'assistant', content: 'Partial answer' },
      { role: 'system', content: JSON.stringify(event), meta: event },
    ])
    const view = render(<GideonChatRuntimeProvider sessionId="stopped-history" turns={turns} streaming={false}
      onSend={() => {}} onStop={() => {}} onEdit={() => {}} onReload={() => {}}>
      <ThreadTranscript components={{ UserMessage: UserSlot, AssistantMessage: AssistantSlot }}/>
    </GideonChatRuntimeProvider>)
    expect(view.container.querySelectorAll('[data-slot="stopped-run"]')).toHaveLength(1)
    expect(view.container.textContent?.match(/Partial answer/g)).toHaveLength(1)
    expect(screen.getByText('Stopped')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Continue' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Discard' })).toBeNull()
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

  it('keeps explicit live tool success and failure without inferring a missing status', () => {
    const segments: Segment[] = [
      { kind: 'tool', id: 'one', tool: 'Read', done: false },
      { kind: 'tool', id: 'two', tool: 'Read', done: false, ok: false },
    ]
    const succeeded = applyLiveToolResult(segments, { tool_call_id: 'one', output: 'File opened', ok: true })
    expect(succeeded[0]).toMatchObject({ done: true, output: 'File opened', ok: true })
    expect(succeeded[1]).toBe(segments[1])
    const failed = applyLiveToolResult(succeeded, { tool_call_id: 'one', output: 'Denied', ok: false })
    expect(failed[0]).toMatchObject({ done: true, output: 'Denied', ok: false })
    const unknown = applyLiveToolResult(segments, { tool_call_id: 'one', output: 'Old event' })
    expect((unknown[0] as Extract<Segment, {kind: 'tool'}>).ok).toBeUndefined()
    const retained = applyLiveToolResult(segments, { tool_call_id: 'two', output: 'Legacy event' })
    expect((retained[1] as Extract<Segment, {kind: 'tool'}>).ok).toBe(false)
    const rich = applyLiveToolResult([...segments, { kind: 'text', text: 'Still answering' }], {
      tool_call_id: 'one', output: 'A result', content_type: 'text/plain', raw_ref: 'result-1',
      truncated: false, original_length: 0, recovery_hints: ['Open result'],
      agent_error: { code: 'DENIED', what: 'Denied', why: 'Policy', fix: 'Ask owner' }, ok: true,
    })
    expect(rich[0]).toMatchObject({ contentType: 'text/plain', rawRef: 'result-1', truncated: false,
      originalLength: 0, recoveryHints: ['Open result'], ok: true })
    expect(rich[2]).toMatchObject({ kind: 'text', text: 'Still answering' })
    const emptyHints = applyLiveToolResult(rich, { tool_call_id: 'one', recovery_hints: [] })
    expect(emptyHints[0]).toMatchObject({ recoveryHints: ['Open result'], ok: true })
    expect(applyLiveToolResult(segments, { output: 'Unmatched' })).toEqual(segments)
  })

  it('shows a real assistant error without advertising an unavailable retry action', () => {
    const { container } = render(<ThreadErrorSegment text="Provider request failed" onSetupModel={() => {}}/>)
    expect(container.querySelector('[data-slot="error-state"]')).toBeTruthy()
    expect(screen.getByRole('alert').textContent).toContain('Provider request failed')
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
  })

  it('renders persisted file changes once, hides truncated counts, and opens the actual path', () => {
    const turns = hydrateTurns([{ role: 'assistant', content: 'Updated two files', ts: '2026-09-26T12:05:00Z', meta: {
      file_changes: [
        { path: 'src/main.ts', before: 'const value = 1\n', after: 'const value = 2\n' },
        { path: 'README.md', before: 'Earlier notes\n… [truncated]', after: 'Current notes\n' },
      ],
    } }])
    function Host() {
      const [opened, setOpened] = useState('')
      function FileAssistantSlot() {
        const id = useMessage((message) => message.id)
        const record = useGideonTurnByAuiId().get(id)
        return <MessagePrimitive.Root>
          {record && <MessageAssistant fileChanges={record.turn.fileChanges} onOpenFile={setOpened}>{turnText(record.turn)}</MessageAssistant>}
        </MessagePrimitive.Root>
      }
      return <GideonChatRuntimeProvider sessionId="persisted-files" turns={turns} streaming={false}
        onSend={() => {}} onStop={() => {}} onEdit={() => {}} onReload={() => {}}>
        <ThreadTranscript components={{ AssistantMessage: FileAssistantSlot }}/>
        <output data-testid="opened-file">{opened}</output>
      </GideonChatRuntimeProvider>
    }
    const { container } = render(<Host/>)
    expect(container.querySelectorAll('[data-slot="file-tree"]')).toHaveLength(1)
    expect(screen.getByText('2 files changed')).toBeTruthy()
    expect(container.querySelector('[data-slot="file-tree"]')?.firstElementChild?.textContent).toBe('2 files changed')
    expect(screen.getByTitle('README.md').textContent).not.toMatch(/[+−]\d/)
    expect(container.querySelectorAll('[data-slot="reviewable-diff"]')).toHaveLength(1)
    expect(screen.getByText('Applied')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^(Keep|Discard|Apply)/ })).toBeNull()
    fireEvent.click(screen.getByTitle('src/main.ts'))
    expect(screen.getByTestId('opened-file').textContent).toBe('src/main.ts')
  })
})
