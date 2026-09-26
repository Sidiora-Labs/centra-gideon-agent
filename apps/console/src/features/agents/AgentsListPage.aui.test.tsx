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
  name: 'maker', provider: '', description: '', skills: [], tools: [], triggers: [], active_sessions: 0,
}
const catalog = { agents: [scout, maker], default_agent: 'scout' }

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
  })

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
})
