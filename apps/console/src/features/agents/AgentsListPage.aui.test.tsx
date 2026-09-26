import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { SavedAgent } from '../../shared/data/api'

const scout: SavedAgent = {
  name: 'scout', provider: 'acp:claude-code', description: 'Investigates customer questions',
  model: 'opus', reserved: true, skills: ['search', 'calendar'], tools: ['web'],
  triggers: ['daily', 'manual'], active_sessions: 3, running_sessions: 1,
}
const maker: SavedAgent = {
  name: 'maker', provider: '',
}
let catalog = { agents: [scout, maker], default_agent: 'scout' }

async function mount(query: Record<string, string> = {}) {
  const setQuery = vi.fn()
  const onCreate = vi.fn()
  const { AgentsListPage } = await import('./AgentsListPage')
  render(<AgentsListPage query={query} setQuery={setQuery} onCreate={onCreate} />)
  return { setQuery, onCreate }
}

beforeEach(() => {
  vi.resetModules()
  sessionStorage.clear()
  catalog = { agents: [scout, maker], default_agent: 'scout' }
  vi.doMock('../../shared/data/api', async orig => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      agents: async () => catalog,
      agentProviders: async () => [],
      syncAgents: async () => ({ ok: true }),
    },
  }))
})

describe('Agents list uses the recorded AgentCard', () => {
  it('shows one card with actual provider, default, built-in, skill, tool, trigger, and session facts', async () => {
    await mount()
    const card = await screen.findByRole('button', { name: 'scout' })
    expect(card).toHaveAttribute('data-slot', 'agent-card')
    expect(card).toHaveAttribute('tabindex', '0')
    expect(screen.getAllByText('scout')).toHaveLength(1)
    expect(within(card).getByText('Claude Code')).toBeInTheDocument()
    expect(within(card).getByText('Investigates customer questions')).toBeInTheDocument()
    expect(within(card).getByText('opus')).toBeInTheDocument()
    expect(within(card).getByText('search')).toBeInTheDocument()
    expect(within(card).getByText('calendar')).toBeInTheDocument()
    expect(within(card).getByText('default')).toBeInTheDocument()
    expect(within(card).getByText('built-in')).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: '2 skills' })).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: '1 tool' })).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: '2 triggers' })).toBeInTheDocument()
    expect(within(card).getByText('1 running · 3 active')).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: /connect/i })).toBeNull()
    expect(within(card).queryByText(/undefined|vundefined/i)).toBeNull()
  }, 45_000)

  it('keeps an agent without optional metadata concise and does not infer reserved or active state', async () => {
    await mount()
    const card = await screen.findByRole('button', { name: 'maker' })
    expect(within(card).getByText('Native')).toBeInTheDocument()
    expect(within(card).queryByText('default')).toBeNull()
    expect(within(card).queryByText('built-in')).toBeNull()
    expect(within(card).queryByRole('img', { name: /skills|tools|triggers/ })).toBeNull()
    expect(within(card).queryByText(/active|running/)).toBeNull()
    expect(within(card).queryByRole('button', { name: /connect/i })).toBeNull()
  })

  it('opens the same native detail address by pointer, Enter, and Space', async () => {
    const { setQuery } = await mount()
    const card = await screen.findByRole('button', { name: 'scout' })
    fireEvent.click(card)
    expect(setQuery).toHaveBeenLastCalledWith({ open: 'native:scout', edit: null })
    card.focus()
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(setQuery).toHaveBeenCalledTimes(2)
    fireEvent.keyDown(card, { key: ' ' })
    expect(setQuery).toHaveBeenCalledTimes(3)
    expect(setQuery).toHaveBeenLastCalledWith({ open: 'native:scout', edit: null })
  })

  it('preserves the keyboard context menu Open action on the focused card', async () => {
    const { setQuery } = await mount()
    const card = await screen.findByRole('button', { name: 'maker' })
    card.focus()
    fireEvent.keyDown(card, { key: 'F10', shiftKey: true })
    const menu = await screen.findByRole('menu')
    await userEvent.click(within(menu).getByRole('menuitem', { name: 'Open' }))
    expect(setQuery).toHaveBeenCalledWith({ open: 'native:maker', edit: null })
  })

  it('keeps search filtering and the real New agent action around the card list', async () => {
    const { onCreate } = await mount({ q: 'maker' })
    expect(await screen.findByRole('button', { name: 'maker' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'scout' })).toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'New agent' }))
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
  })

  it('uses singular and plural counts from another recorded agent without inventing a running session', async () => {
    catalog = { agents: [{
      name: 'runner', provider: 'acp:claude-code', skills: ['dispatch'], tools: ['web', 'calendar'],
      triggers: ['daily'], active_sessions: 2, running_sessions: 0,
    }], default_agent: 'scout' }
    await mount()
    const card = await screen.findByRole('button', { name: 'runner' })
    expect(within(card).getByRole('img', { name: '1 skill' })).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: '2 tools' })).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: '1 trigger' })).toBeInTheDocument()
    expect(within(card).getByText('2 active')).toBeInTheDocument()
    expect(within(card).queryByText(/running|default|built-in/)).toBeNull()
  })

  it('shows the matching empty state when search excludes all recorded native agents', async () => {
    await mount({ q: 'absent' })
    expect((await screen.findAllByText('No matching agents')).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'scout' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'maker' })).toBeNull()
  })

  it('shows the native empty state and creation action when the catalog has no saved agents', async () => {
    catalog = { agents: [], default_agent: '' }
    const { onCreate } = await mount()
    expect(await screen.findByText('No native agents')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'scout' })).toBeNull()
    const actions = screen.getAllByRole('button', { name: 'New agent' })
    await userEvent.click(actions[actions.length - 1])
    expect(onCreate).toHaveBeenCalledTimes(1)
  })

  it('keeps donor skill descriptions and the recorded child slot visible when supplied', async () => {
    const { AgentCard } = await import('../../shared/vendor/assistant-ui/elements/agent-card')
    render(<AgentCard name="described" provider="Native" description="Recorded purpose"
      skills={[{ name: 'search', description: 'Finds customer records' }]}>
      <span>2 active</span>
    </AgentCard>)
    const card = screen.getByText('described').closest('[data-slot="agent-card"]')!
    expect(within(card as HTMLElement).getByText('Recorded purpose')).toBeInTheDocument()
    expect(within(card as HTMLElement).getByText('Finds customer records')).toBeInTheDocument()
    expect(within(card as HTMLElement).getByText('2 active')).toBeInTheDocument()
  })
})
