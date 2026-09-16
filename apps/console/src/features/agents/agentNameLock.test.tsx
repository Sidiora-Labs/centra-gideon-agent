import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render } from '@testing-library/react'


vi.mock('../../shared/data/api', () => ({
  api: new Proxy({}, { get: () => () => Promise.resolve(null) }),
}))
vi.mock('../../shared/data/agents', () => ({
  useActiveChatModelOptions: () => ({ options: [], loading: false }),
}))

import { AgentForm, emptyDraft, type AgentDraft } from './AgentForm'

function Harness({ locked }: { locked: boolean }) {
  const [draft, setDraft] = useState<AgentDraft>({ ...emptyDraft(), name: 'research-assistant' })
  return <AgentForm draft={draft} onChange={setDraft} nameLocked={locked} />
}

const nameField = (container: HTMLElement) =>
  container.querySelector('input[placeholder="research-assistant"]') as HTMLInputElement

describe('agent Name lock is visible and real (#665)', () => {
  it('locked: the field is natively disabled, carries the reason, and the hint explains it', () => {
    const { container, getByText } = render(<Harness locked />)
    const field = nameField(container)
    expect(field.disabled).toBe(true)
    expect(field.title).toBe('Names are fixed after creation')
    expect(getByText(/a rename would orphan every reference/)).toBeTruthy()
  })

  it('locked: typing cannot move the draft (the primitive blocks it, not a silent handler)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default
    const seen: string[] = []
    function Spy() {
      const [draft, setDraft] = useState<AgentDraft>({ ...emptyDraft(), name: 'research-assistant' })
      return <AgentForm draft={draft} onChange={(d) => { seen.push(d.name); setDraft(d) }} nameLocked />
    }
    const { container } = render(<Spy />)
    const field = nameField(container)
    await userEvent.type(field, 'zz-probe')
    expect(seen).toEqual([])
    expect(field.value).toBe('research-assistant')
  })

  it('unlocked: typing still works and still normalizes to the slug shape', () => {
    const { container, getByText } = render(<Harness locked={false} />)
    const field = nameField(container)
    expect(field.disabled).toBe(false)
    fireEvent.change(field, { target: { value: 'My Cool Agent!' } })
    expect(field.value).toBe('my-cool-agent-')
    expect(getByText(/Lowercase, hyphenated/)).toBeTruthy()
  })
})
