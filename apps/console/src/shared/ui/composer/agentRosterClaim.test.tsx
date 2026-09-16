import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AgentPill } from './controls'
import type { ComposerData } from './types'


const base: ComposerData = { agents: [], providers: [], discovered: {}, models: [] }

function openPicker() {
  return userEvent.click(screen.getByRole('button', { name: /^Agent/ }))
}

describe('the agent picker never states an emptiness it could not read', () => {
  it('an in-flight read says it is loading, NOT that no agents exist', async () => {
    render(<AgentPill data={{ ...base, ready: false }} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.getByText(/Loading agents/i)).toBeInTheDocument()
    expect(screen.queryByText('No agents available'), 'an unread roster is not an empty one').toBeNull()
  })

  it('a FAILED read says so and offers a retry — even though `ready` is true', async () => {
    const retry = vi.fn()
    render(<AgentPill data={{ ...base, ready: true, agentsErr: new Error('gateway down'), retry }} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/Couldn’t load your agents/i)).toBeInTheDocument()
    expect(screen.queryByText('No agents available'), 'a failed read must not claim the setup is empty').toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /Try again/i }))
    expect(retry, 'the retry must be wired, or the honest message is a dead end').toHaveBeenCalledTimes(1)
  })

  it('a GENUINELY empty roster still says "No agents available" — the true claim survives', async () => {
    render(<AgentPill data={{ ...base, ready: true, agentsErr: null }} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.getByText('No agents available')).toBeInTheDocument()
  })

  it('a populated roster shows the agents and none of the three empty messages', async () => {
    const data = { ...base, ready: true, agents: [{ name: 'researcher', description: 'd' }] } as unknown as ComposerData
    render(<AgentPill data={data} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.getByText('researcher')).toBeInTheDocument()
    expect(screen.queryByText('No agents available')).toBeNull()
    expect(screen.queryByText(/Loading agents/i)).toBeNull()
    expect(screen.queryByText(/Couldn’t load your agents/i)).toBeNull()
  })

  it('a host that passes no `ready` is NOT stuck on loading', async () => {
    render(<AgentPill data={base} value="" onSelect={vi.fn()} />)
    await openPicker()
    expect(screen.queryByText(/Loading agents/i)).toBeNull()
    expect(screen.getByText('No agents available')).toBeInTheDocument()
  })
})

describe('the hook reports the failure it used to swallow', () => {
  const src = () => readFileSync(join(process.cwd(), "src/shared/data/useComposerData.ts"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('a rejected agents read sets agentsErr', () => {
    expect(src(), 'the rejected leg must be recorded, not dropped').toMatch(/setAgentsErr\(ag\.reason/)
    expect(src(), 'and it must be exposed to the picker').toMatch(/return \{[^}]*agentsErr/)
  })

  it('a successful retry clears a previous failure', () => {
    expect(src()).toMatch(/setAgentsErr\(null\)/)
  })
})
