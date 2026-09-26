import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useAui, type AppendMessage, type AssistantClient } from '@assistant-ui/react'
import type { ChatHistoryMsg } from '../../shared/data/api'
import { appendThinking, assistantTurn, guardrailNoticeForTool, hydrateTurns, userTurn, type ChatTurn, type HistMsg } from './chatTypes'
import {
  GideonChatRuntimeProvider, appendText, convertGideonTurn, gideonAuiId, makeGideonQueueAdapter, reloadLatestGideonAnswer,
  useGideonTurnByAuiId, type GideonChatRuntimeProps, type GideonTurnRef,
} from './auiRuntime'

afterEach(cleanup)

function setup(overrides: Partial<GideonChatRuntimeProps> = {}) {
  const callbacks = {
    onSend: vi.fn(async (_text: string) => {}),
    onStop: vi.fn(async () => {}),
    onEdit: vi.fn(async (_index: number, _text: string) => {}),
    onReload: vi.fn(async () => {}),
    onQueue: vi.fn(async (_text: string, _lane: 'queue' | 'steer') => {}),
    onQueueRemove: vi.fn(async (_id: string) => {}),
    onQueueEdit: vi.fn(async (_id: string, _text: string) => {}),
    onQueueInterrupt: vi.fn(async (_id: string) => {}),
    onSwitchSession: vi.fn(async (_key: string) => {}),
    onNewSession: vi.fn(async () => {}),
  }
  const initial: GideonChatRuntimeProps = {
    sessionId: 'session/a',
    turns: [userTurn('Hello', '2026-09-26T12:00:00Z'), assistantTurn('Hi')],
    streaming: false,
    queued: [],
    sessions: [{ key: 'session/a', title: 'Current' }, { key: 'session/b', title: 'Older' }],
    suggestions: ['Ask for a summary'],
    ...callbacks,
    children: null,
    ...overrides,
  }
  let aui: AssistantClient
  let turnByAuiId: ReadonlyMap<string, GideonTurnRef>
  function Capture() {
    aui = useAui()
    turnByAuiId = useGideonTurnByAuiId()
    return null
  }
  const draw = (props: GideonChatRuntimeProps) => render(
    <GideonChatRuntimeProvider {...props}><Capture /></GideonChatRuntimeProvider>,
  )
  const rendered = draw(initial)
  return {
    aui: () => aui!,
    turnByAuiId: () => turnByAuiId!,
    callbacks,
    rerender: (next: Partial<GideonChatRuntimeProps>) => rendered.rerender(
      <GideonChatRuntimeProvider {...initial} {...next}><Capture /></GideonChatRuntimeProvider>,
    ),
  }
}

describe('Gideon assistant-ui runtime', () => {
  it('preserves session and ordinal identity while live assistant segments change', async () => {
    const user = userTurn('Explain', '2026-09-26T12:00:00Z')
    user.optimized = 'Explain with examples'
    user.pastes = [{ seq: 1, lines: 3, content: 'pasted context' }]
    user.rewound = [{ messages: [{ role: 'assistant', content: 'earlier answer' }] }]
    const first = assistantTurn('partial')
    const ui = setup({ turns: [user, first], streaming: true })
    const userId = gideonAuiId('session/a', 0)
    const assistantId = gideonAuiId('session/a', 1)

    expect(ui.aui().thread.getState().messages.map((message) => message.id)).toEqual([userId, assistantId])
    expect(ui.aui().thread.getState().isRunning).toBe(true)
    expect(ui.turnByAuiId().get(userId)).toEqual({ turn: user, index: 0 })
    expect(ui.turnByAuiId().get(userId)?.turn.optimized).toBe('Explain with examples')
    expect(ui.turnByAuiId().get(userId)?.turn.pastes?.[0].content).toBe('pasted context')
    expect(ui.turnByAuiId().get(userId)?.turn.rewound?.[0].messages[0].content).toBe('earlier answer')

    const grown = assistantTurn('partial answer')
    await act(async () => ui.rerender({ turns: [user, grown], streaming: true }))
    expect(ui.aui().thread.getState().messages[1].id).toBe(assistantId)
    expect(ui.turnByAuiId().get(assistantId)?.turn).toBe(grown)
    expect(ui.aui().thread.getState().messages[1].content).toEqual([{ type: 'text', text: 'partial answer' }])
    await act(async () => ui.rerender({ turns: [user, grown], streaming: false }))
    expect(ui.aui().thread.getState().messages[1]).toMatchObject({ status: { type: 'complete', reason: 'stop' } })
  })

  it('keeps complete history when the current session changes', async () => {
    const ui = setup()
    const previousId = gideonAuiId('session/a', 1)
    const history: ChatTurn[] = [userTurn('Old question'), assistantTurn('Old answer'), userTurn('New question'), assistantTurn('New answer')]
    await act(async () => ui.rerender({ sessionId: 'session/b', turns: history }))
    expect(ui.aui().thread.getState().messages.map((message) => message.id)).toEqual(history.map((_, index) => gideonAuiId('session/b', index)))
    expect(ui.turnByAuiId().has(previousId)).toBe(false)
    expect(ui.turnByAuiId().get(gideonAuiId('session/b', 3))?.turn).toBe(history[3])
  })

  it('keeps gateway message identity when hydration changes visible history indices', async () => {
    const history: HistMsg[] = [
      { role: 'user', content: 'Inspect the file', ts: '2026-09-26T12:00:00Z' },
      { role: 'assistant', content: 'Opening it' },
    ]
    const first = hydrateTurns(history, true)
    const ui = setup({ sessionId: 'gateway/thread-7', turns: first, streaming: true })
    const messageId = gideonAuiId('gateway/thread-7', 1)
    expect(first[1].visibleIndex).toBe(1)
    expect(ui.aui().thread.getState().messages[1].id).toBe(messageId)

    const grown = hydrateTurns([...history, { role: 'assistant', content: 'Found it' }], true)
    expect(grown).toHaveLength(first.length)
    expect(grown[1].visibleIndex).toBe(2)
    await act(async () => ui.rerender({ turns: grown, streaming: true }))
    expect(ui.aui().thread.getState().messages[1].id).toBe(messageId)
    expect(ui.turnByAuiId().get(messageId)).toEqual({ turn: grown[1], index: 1 })
    expect(ui.aui().thread.getState().messages[1].content).toEqual([
      { type: 'text', text: 'Opening it' }, { type: 'text', text: 'Found it' },
    ])
  })

  it('projects live reasoning and a gateway tool error through the connected runtime', async () => {
    const history: HistMsg[] = [
      { role: 'user', content: 'Read report.txt' },
      { role: 'assistant', content: 'Checking' },
      { role: 'tool', content: 'read_file', meta: { tool_call_id: 'call-7', tool: 'read_file', input: '{"path":"report.txt"}' } },
    ]
    const first = hydrateTurns(history, true)
    first[1] = { ...first[1], segments: [
      ...appendThinking([first[1].segments[0]], 'Looking for the report'),
      ...first[1].segments.slice(1),
    ] }
    const ui = setup({ turns: first, streaming: true })
    const messageId = gideonAuiId('session/a', 1)
    const streamingParts = ui.aui().thread.getState().messages[1].content
    expect(streamingParts).toHaveLength(3)
    expect(streamingParts[0]).toEqual({ type: 'text', text: 'Checking' })
    expect(streamingParts[1]).toEqual({ type: 'reasoning', text: 'Looking for the report' })
    expect(streamingParts[2]).toMatchObject({
      type: 'tool-call', toolCallId: 'call-7', toolName: 'read_file',
      argsText: '{"path":"report.txt"}', args: { path: 'report.txt' }, isError: false,
    })
    expect(streamingParts[2]).toHaveProperty('result', undefined)

    const agentError = { code: 'ENOENT', what: 'File missing', why: 'No report exists', fix: 'Check the path' }
    const finished = hydrateTurns([...history, {
      role: 'tool', content: 'read_file', meta: { tool_call_id: 'call-7', done: true, output: 'not found', agent_error: agentError },
    }], false)
    finished[1] = { ...finished[1], segments: [
      ...appendThinking([finished[1].segments[0]], 'Looking for the report'),
      ...finished[1].segments.slice(1),
    ] }
    await act(async () => ui.rerender({ turns: finished, streaming: false }))
    const message = ui.aui().thread.getState().messages[1]
    expect(message.id).toBe(messageId)
    expect(message.content[1]).toEqual({ type: 'reasoning', text: 'Looking for the report' })
    expect(message.content[2]).toMatchObject({
      type: 'tool-call', toolCallId: 'call-7', toolName: 'read_file',
      argsText: '{"path":"report.txt"}', args: { path: 'report.txt' },
      result: 'not found', isError: true,
    })
    expect(ui.turnByAuiId().get(messageId)?.turn.segments[1]).toEqual({ kind: 'thinking', text: 'Looking for the report' })
    expect(ui.turnByAuiId().get(messageId)?.turn.segments[2]).toMatchObject({ id: 'call-7', agentError, done: true })
  })

  it('preserves gateway file-change snapshots without inventing review actions', () => {
    const changes = [
      { path: 'src/report.py', before: 'old value', after: 'new value\n… [truncated]' },
      { path: 'README.md', before: '', after: 'Added readme' },
    ]
    const history: ChatHistoryMsg[] = [
      { role: 'user', content: 'Update the files' },
      { role: 'assistant', content: 'Editing' },
      { role: 'assistant', content: 'Done', meta: { file_changes: changes } },
    ]
    const turns = hydrateTurns(history)
    const ui = setup({ turns })
    const messageId = gideonAuiId('session/a', 1)
    expect(turns[1].fileChanges).toBe(changes)
    expect(ui.turnByAuiId().get(messageId)?.turn.fileChanges).toBe(changes)
    expect(ui.aui().thread.getState().messages[1].content).toEqual([
      { type: 'text', text: 'Editing' },
      { type: 'text', text: 'Done' },
      { type: 'data', name: 'gideon-file-changes', data: changes },
    ])

    const withoutChanges = hydrateTurns(history.slice(0, 2))
    expect(withoutChanges[1].fileChanges).toBeUndefined()
    expect(convertGideonTurn(withoutChanges[1], 1, 'session/a', false).content).toEqual([
      { type: 'text', text: 'Editing' },
    ])
  })

  it('carries a computer-use denial through live and hydrated tool messages', async () => {
    const error = {
      code: 'ERR_COMPUTER_USE_DISABLED',
      what: 'computer_click refused: desktop computer use is OFF on this machine — no enable file.',
      why: 'Desktop input requires operator arming.',
      fix: 'A human must write the out-of-band enable file.',
    }
    const live: ChatTurn[] = [userTurn('Click'), {
      role: 'assistant', segments: [{ kind: 'tool', id: 'computer-1', tool: 'computer_click',
        output: error.what, done: true, agentError: error, ok: false }],
    }]
    const ui = setup({ turns: live })
    expect(guardrailNoticeForTool(live[1].segments[0] as Extract<ChatTurn['segments'][number], { kind: 'tool' }>))
      .toEqual({ code: error.code, reason: error.what })
    expect(ui.aui().thread.getState().messages[1].content[1]).toEqual({
      type: 'data', name: 'gideon-guardrail-notice', data: { code: error.code, reason: error.what },
    })

    const history = hydrateTurns([
      { role: 'user', content: 'Click' },
      { role: 'tool', content: 'computer_click', meta: {
        tool_call_id: 'computer-1', done: true, output: error.what, agent_error: error, ok: false,
      } },
    ])
    await act(async () => ui.rerender({ turns: history }))
    expect(ui.aui().thread.getState().messages[1].content[1]).toMatchObject({
      type: 'data', name: 'gideon-guardrail-notice', data: { code: error.code, reason: error.what },
    })
    const unrelated = { ...error, code: 'ERR_COMPUTER_USE_DRIVER_FAILED' }
    expect(guardrailNoticeForTool({ kind: 'tool', id: 'driver-1', tool: 'computer_click', done: true, agentError: unrelated })).toBeNull()
  })

  it('converts actual text, reasoning, tool, activity, approval and error segments without inventing results', () => {
    const turn: ChatTurn = {
      role: 'assistant', ts: '2026-09-26T12:00:00Z', segments: [
        { kind: 'text', text: 'Answer' },
        { kind: 'thinking', text: 'Checking' },
        { kind: 'tool', id: 'tool-1', tool: 'read_file', input: '{"path":"a"}', done: false },
        { kind: 'activity', text: 'Reading' },
        { kind: 'approval', id: 'approval-1', tool: 'write_file' },
        { kind: 'error', text: 'Failed' },
      ],
    }
    const converted = convertGideonTurn(turn, 2, 'session/a', false)
    expect(converted.id).toBe('gideon:session%2Fa:2')
    expect(converted.createdAt?.toISOString()).toBe(new Date(turn.ts!).toISOString())
    expect(converted.status).toEqual({ type: 'incomplete', reason: 'error' })
    expect(converted.content).toEqual([
      { type: 'text', text: 'Answer' },
      { type: 'reasoning', text: 'Checking' },
      { type: 'tool-call', toolCallId: 'tool-1', toolName: 'read_file', argsText: '{"path":"a"}', result: undefined, isError: false },
      { type: 'data', name: 'gideon-activity', data: turn.segments[3] },
      { type: 'data', name: 'gideon-approval', data: turn.segments[4] },
      { type: 'data', name: 'gideon-error', data: turn.segments[5] },
    ])
    expect(convertGideonTurn(turn, 2, 'session/a', true).status).toEqual({ type: 'running' })
    expect(convertGideonTurn({ ...turn, ts: 'bad-date' }, 2, null, false).createdAt).toBeUndefined()
    expect(convertGideonTurn({ role: 'user', segments: [{ kind: 'text', text: 'Question' }] }, 0, null, true)).toMatchObject({
      id: 'gideon:new:0', role: 'user', content: [{ type: 'text', text: 'Question' }],
    })
    expect(convertGideonTurn({ role: 'assistant', segments: [
      { kind: 'tool', id: 'tool-2', tool: 'run', done: true, output: 'failed', ok: false },
    ] }, 3, 'session/a', false).content).toEqual([{
      type: 'tool-call', toolCallId: 'tool-2', toolName: 'run', argsText: '{}', result: 'failed', isError: true,
    }])
  })

  it('delegates a normal send to Gideon once and rejects attachments it cannot transport', async () => {
    const ui = setup()
    await act(async () => ui.aui().thread.append('  Hello from assistant-ui  '))
    await waitFor(() => expect(ui.callbacks.onSend).toHaveBeenCalledExactlyOnceWith('Hello from assistant-ui'))
    const imageMessage: AppendMessage = {
      role: 'user', content: [{ type: 'image', image: 'https://example.com/a.png' }], attachments: [],
      metadata: { custom: {} }, createdAt: new Date(0), parentId: null, sourceId: null, runConfig: undefined,
    }
    expect(() => appendText(imageMessage)).toThrow('Gideon composer must handle non-text attachments before sending')
    expect(ui.callbacks.onSend).toHaveBeenCalledTimes(1)
  })

  it('delegates edit, reload and stop once to the existing Gideon handlers', async () => {
    const ui = setup()
    const userId = gideonAuiId('session/a', 0)
    await act(async () => ui.aui().thread.append({ sourceId: userId, content: [{ type: 'text', text: 'Revised' }] }))
    await waitFor(() => expect(ui.callbacks.onEdit).toHaveBeenCalledExactlyOnceWith(0, 'Revised'))
    await act(async () => ui.aui().thread.startRun({ parentId: userId }))
    await waitFor(() => expect(ui.callbacks.onReload).toHaveBeenCalledTimes(1))
    await expect(reloadLatestGideonAnswer(null, 'session/a', [userTurn('Hello'), assistantTurn('Hi')], ui.callbacks.onReload))
      .rejects.toThrow('Gideon can regenerate only the latest answer')
    await expect(reloadLatestGideonAnswer(null, 'session/a', [], ui.callbacks.onReload))
      .rejects.toThrow('Gideon can regenerate only the latest answer')
    expect(ui.callbacks.onReload).toHaveBeenCalledTimes(1)
    await act(async () => ui.rerender({ streaming: true }))
    await act(async () => ui.aui().thread.cancelRun())
    await waitFor(() => expect(ui.callbacks.onStop).toHaveBeenCalledTimes(1))
  })

  it('rejects an assistant turn as an edit target through the external-store runtime', async () => {
    const ui = setup()
    const runtime = ui.aui().thread.__internal_getRuntime?.() as unknown as {
      __internal_threadBinding: { getState(): { append(message: AppendMessage): Promise<void> } }
    }
    const edit: AppendMessage = {
      role: 'user', content: [{ type: 'text', text: 'Wrong target' }], attachments: [],
      metadata: { custom: {} }, createdAt: new Date(0),
      parentId: gideonAuiId('session/a', 0), sourceId: gideonAuiId('session/a', 1), runConfig: undefined,
    }
    await expect(runtime.__internal_threadBinding.getState().append(edit))
      .rejects.toThrow('Cannot edit a turn outside this Gideon session')
    expect(ui.callbacks.onEdit).not.toHaveBeenCalled()
  })

  it('routes an active send into the existing steer lane and carries the real queued IDs', async () => {
    const ui = setup({ streaming: true, queued: [{ id: 'queue-1', content: 'Next prompt' }] })
    await act(async () => ui.aui().thread.append('Steer this answer'))
    await waitFor(() => expect(ui.callbacks.onQueue).toHaveBeenCalledExactlyOnceWith('Steer this answer', 'steer'))
    expect(ui.callbacks.onSend).not.toHaveBeenCalled()
    expect(ui.aui().thread.composer().getState().queue.map((item) => item.id)).toContain('queue-1')
    await act(async () => ui.aui().thread.composer().queueItem({ id: 'queue-1' }).remove())
    expect(ui.callbacks.onQueueRemove).toHaveBeenCalledExactlyOnceWith('queue-1')
    await act(async () => ui.aui().thread.composer().queueItem({ id: 'queue-1' }).move({ lane: 'steer', insertAfter: null }))
    expect(ui.callbacks.onQueueInterrupt).toHaveBeenCalledExactlyOnceWith('queue-1')
    expect(() => ui.aui().thread.composer().queueItem({ id: 'queue-1' }).move({ lane: 'queue', insertAfter: 'other' })).toThrow('Gideon does not support queue reordering')
  })

  it('exposes live session navigation and followups through assistant-ui', async () => {
    const ui = setup({ sessions: [
      { key: 'session/a', title: 'Current' },
      { key: 'session/b', title: 'Older' },
      { key: 'session/c', title: 'Archived', lifecycle: 'archived' },
    ] })
    expect(ui.aui().thread.getState().suggestions).toEqual([{ prompt: 'Ask for a summary' }])
    expect(ui.aui().threads.getState().threadIds).toContain('session/b')
    expect(ui.aui().threads.getState().archivedThreadIds).toContain('session/c')
    await act(async () => ui.aui().threads.item({ index: 1 }).switchTo())
    expect(ui.callbacks.onSwitchSession).toHaveBeenCalledExactlyOnceWith('session/b')
  })

  it('does not advertise session-list navigation without both Gideon navigation callbacks', () => {
    const ui = setup({ onNewSession: undefined })
    expect(ui.aui().threads.getState().threadIds).not.toContain('session/b')
    expect(ui.aui().thread.getState().messages.map((message) => message.id)).toEqual([
      gideonAuiId('session/a', 0), gideonAuiId('session/a', 1),
    ])
    expect(ui.callbacks.onSwitchSession).not.toHaveBeenCalled()
  })

  it('keeps new chats and optional queue capabilities explicit', () => {
    const ui = setup({ sessionId: null, turns: [], onQueue: undefined, queued: [{ id: 'pending', content: 'queued' }] })
    expect(ui.aui().thread.getState().messages).toHaveLength(0)
    expect(ui.aui().thread.getState().capabilities.queue).toBe(false)
    expect(gideonAuiId(null, 0)).toBe('gideon:new:0')
    expect(ui.turnByAuiId().size).toBe(0)
  })

  it('maps queue edits and normal queue sends into the existing action callbacks', () => {
    const onQueue = vi.fn()
    const onQueueRemove = vi.fn()
    const onQueueEdit = vi.fn()
    const onQueueInterrupt = vi.fn()
    const args = { queued: [{ id: 'q-1', content: 'first' }], streaming: false, onQueue, onQueueRemove, onQueueEdit, onQueueInterrupt }
    const queue = makeGideonQueueAdapter(args)
    const message: AppendMessage = {
      role: 'user', content: [{ type: 'text', text: '  revised  ' }], attachments: [],
      metadata: { custom: {} }, createdAt: new Date(0), parentId: null, sourceId: null, runConfig: undefined,
    }

    expect(queue?.items).toEqual([{ id: 'q-1', prompt: 'first', parts: [{ type: 'text', text: 'first' }] }])
    queue?.enqueue(message)
    queue?.steer(message)
    queue?.edit('q-1', message)
    queue?.remove('q-1')
    queue?.move('q-1', { lane: 'steer' })
    expect(onQueue.mock.calls).toEqual([['revised', 'queue'], ['revised', 'steer']])
    expect(onQueueEdit).toHaveBeenCalledExactlyOnceWith('q-1', 'revised')
    expect(onQueueRemove).toHaveBeenCalledExactlyOnceWith('q-1')
    expect(onQueueInterrupt).toHaveBeenCalledExactlyOnceWith('q-1')
    expect(makeGideonQueueAdapter({ ...args, queued: [] })).toBeUndefined()
    expect(makeGideonQueueAdapter({ ...args, onQueueRemove: undefined })).toBeUndefined()
  })
})
