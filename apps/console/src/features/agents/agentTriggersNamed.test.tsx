import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'

const hooksMock = vi.fn()

vi.mock('../../shared/data/api', async (importOriginal) => {
  const mod = await importOriginal<typeof import('../../shared/data/api')>()
  return { ...mod, api: { ...mod.api, hooks: (...a: unknown[]) => hooksMock(...a) } }
})

import { NativeAgentDetail } from './AgentDetail'

const HOOK = {
  id: 'e4ec861a', name: 'Log every lesson the translator pipeline learns', event: 'MemoryWrite',
  matcher: '', provider: 'shell', provider_config: {}, timeout: 30, enabled: true,
  last_run: 0, last_status: '', run_count: 0, used_by: [],
}

function agentWith(over: Record<string, unknown> = {}) {
  return {
    name: 'termbase-auditor', description: '', model: '', approval_mode: '',
    system_prompt: '', skills: ['knowledge-grounding'], tools: [], triggers: ['e4ec861a'],
    ...over,
  } as never
}

function mount(agent = agentWith()) {
  return render(
    <NativeAgentDetail
      agent={agent} isDefault={false} editing={false}
      onSaved={() => {}} onDeleted={() => {}} onSetDefault={() => {}} onEditingChange={() => {}}
    />,
  )
}

beforeEach(() => { hooksMock.mockReset() })
afterEach(() => cleanup())

describe('agent detail names its trigger bindings (#629)', () => {
  it('resolves the bound id to the picker\u2019s own "name \u00b7 event" and hides the hex', async () => {
    hooksMock.mockResolvedValue([HOOK])
    mount()
    await waitFor(() => {
      expect(screen.getByText(/Log every lesson the translator pipeline learns \u00b7 MemoryWrite/)).toBeTruthy()
    })
    expect(screen.queryByText('e4ec861a')).toBeNull()
    expect(screen.getByText('knowledge-grounding')).toBeTruthy()
  })

  it('marks a binding whose trigger was deleted as dangling, keeping the id legible', async () => {
    hooksMock.mockResolvedValue([])
    mount()
    await waitFor(() => {
      expect(screen.getByText(/trigger no longer exists/)).toBeTruthy()
    })
    expect(screen.getByText('e4ec861a')).toBeTruthy()
  })

  it('claims nothing when the trigger list could not be fetched', async () => {
    hooksMock.mockRejectedValue(new Error('boom'))
    mount()
    expect(await screen.findByText('e4ec861a')).toBeTruthy()
    expect(screen.queryByText(/trigger no longer exists/)).toBeNull()
  })

  it('does not fetch triggers for an agent with no bindings', async () => {
    hooksMock.mockResolvedValue([HOOK])
    mount(agentWith({ triggers: [] }))
    expect(await screen.findByText('knowledge-grounding')).toBeTruthy()
    expect(hooksMock).not.toHaveBeenCalled()
  })

  it('shows the donor status only when the API supplies a session count', () => {
    hooksMock.mockResolvedValue([])
    const view = mount(agentWith({ triggers: [], running_sessions: 2 }))
    expect(screen.getByText('termbase-auditor: 2 running')).toBeInTheDocument()

    view.rerender(<NativeAgentDetail agent={agentWith({ triggers: [], running_sessions: 0 })}
      isDefault={false} editing={false} onSaved={() => {}} onDeleted={() => {}}
      onSetDefault={() => {}} onEditingChange={() => {}} />)
    expect(screen.getByText('termbase-auditor: idle')).toBeInTheDocument()

    view.rerender(<NativeAgentDetail agent={agentWith({ triggers: [] })}
      isDefault={false} editing={false} onSaved={() => {}} onDeleted={() => {}}
      onSetDefault={() => {}} onEditingChange={() => {}} />)
    expect(screen.queryByText(/termbase-auditor: (idle|\d+ running)/)).toBeNull()
  })
})
