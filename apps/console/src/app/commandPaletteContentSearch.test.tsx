import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ContentSearchData } from './paletteSearch'
import { chatFindPath, memoryRecordPath, projectContentSearch } from './paletteSearch'
import { useHashRoute } from './shell/useHashRoute'
import { ThreadConversationSearch } from '../features/chat/auiThreadSurfaces'
import type { SessionSearchAnswer } from '../shared/data/api'

const answer: SessionSearchAnswer = {
  sessions: [
    { key: 'dashboard_chat-7', title: 'Budget planning', snippet: '…the <<budget>> for Q3…' },
    { key: 'dashboard:chat-7', title: 'Budget planning' },
  ],
  source: 'index',
  searched: { chats: 3210, of: 12005 },
  complete: false,
  index: { indexed: 3210, of: 12005, building: true, long: 0 },
  matched: 2,
}

const data: ContentSearchData = {
  chats: answer,
  episodes: [{ id: 'episode-1', text: 'Talked the budget through', created_at: '2026-09-26' }],
  facts: [{ key: 'budget.limit', value_json: '"4000 a month"' }, { key: 'timezone', value_json: '"Europe/Lisbon"' }],
  knowledge: { items: [{ id: 'knowledge-1', title: 'Budget template', summary: 'A sheet' }], total: 2, limit: 1 },
  tasks: { tasks: [{ id: 'task-1', title: 'Send the budget', status: 'open' }], total: 3 },
}

afterEach(() => {
  cleanup()
  history.replaceState(null, '', '#/dashboard')
})

describe('command palette content result projection', () => {
  it('projects real source envelopes into correct record routes and keeps partial coverage visible', () => {
    const result = projectContentSearch('budget', data)

    expect(result.hits.map((hit) => hit.source)).toEqual(['chats', 'memory', 'memory', 'knowledge', 'tasks'])
    expect(result.hits[0]).toMatchObject({ label: 'Budget planning', detail: '…the budget for Q3…', path: 'chat/chat-7?find=budget' })
    expect(result.hits[1]).toMatchObject({ label: 'budget.limit', path: memoryRecordPath('fact:budget.limit') })
    expect(result.hits[2]).toMatchObject({ label: 'Talked the budget through', path: memoryRecordPath('epi:episode-1') })
    expect(result.hits[3].path).toBe('knowledge/item/knowledge-1')
    expect(result.hits[4].path).toBe('tasks?open=task-1')
    expect(result.sources.chats).toMatchObject({ status: 'partial', message: expect.stringContaining('3,210 of 12,005') })
    expect(result.sources.memory).toMatchObject({ status: 'partial', message: expect.stringContaining('limited to 20') })
    expect(result.sources.knowledge).toMatchObject({ status: 'partial', message: expect.stringContaining('2 knowledge matches') })
    expect(result.sources.tasks).toMatchObject({ status: 'partial', message: expect.stringContaining('3 task matches') })
    expect(result.hits.some((hit) => hit.label === 'timezone')).toBe(false)
  })

  it('discloses an unavailable source without dropping hits from other sources', () => {
    const result = projectContentSearch('budget', {
      ...data,
      knowledge: undefined,
      failures: { knowledge: 'knowledge index unavailable' },
    })
    expect(result.sources.knowledge.status).toBe('unavailable')
    expect(result.sources.knowledge.message).toContain("Couldn't search your knowledge")
    expect(result.hits.some((hit) => hit.source === 'chats')).toBe(true)
    expect(result.hits.some((hit) => hit.source === 'tasks')).toBe(true)
  })

  it('opens a deep-linked chat find query through the real hash router and existing search surface', async () => {
    const route = renderHook(() => useHashRoute('dashboard'))
    const path = chatFindPath(answer.sessions[0].key, 'budget')
    act(() => route.result.current.navigate(path))
    await waitFor(() => expect(route.result.current).toMatchObject({ route: 'chat', sub: 'chat-7', query: { find: 'budget' } }))

    const turnAnchor = document.createElement('div')
    document.body.appendChild(turnAnchor)
    const view = render(
      <ThreadConversationSearch
        turns={[{ role: 'assistant', segments: [{ kind: 'text', text: 'The budget is ready.' }] }]}
        nodeOf={() => turnAnchor}
        initialQuery={route.result.current.query.find}
        onClose={() => route.result.current.setQuery({ find: null }, { replace: true })}
      />,
    )
    expect((screen.getByRole('textbox', { name: 'Find in conversation' }) as HTMLInputElement).value).toBe('budget')
    expect(screen.getByText('budget', { exact: true })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Close find' }))
    await waitFor(() => expect(route.result.current.query.find).toBeUndefined())
    view.unmount()
    turnAnchor.remove()
  })

  it('bounds deep-link query text at the search control', async () => {
    const long = 'x'.repeat(250)
    const { result } = renderHook(() => useHashRoute('dashboard'))
    act(() => result.current.navigate(chatFindPath('chat-8', long)))
    await waitFor(() => expect(result.current.query.find).toHaveLength(200))
  })
})
