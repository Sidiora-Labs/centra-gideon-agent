import { describe, it, expect, beforeEach, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { PolicyOverridesPanel, POLICY_KNOBS } from './PolicyOverridesPanel'
import { PRELAUNCH_RUN_STATUSES, isPrelaunch } from './workflowMeta'


let calls: Array<{ id: string; overrides: Record<string, unknown> }>
let respond: (overrides: Record<string, unknown>) => Record<string, unknown>

vi.mock('../../shared/data/api', async (importActual) => {
  const actual = await importActual<typeof import('../../shared/data/api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      setWorkflowRunPolicyOverrides: (id: string, overrides: Record<string, unknown>) => {
        calls.push({ id, overrides })
        return Promise.resolve({ run_id: id, status: 'draft', policy_overrides: respond(overrides) })
      },
    },
  }
})

beforeEach(() => {
  calls = []
  respond = (overrides) => overrides
})

describe('the editor covers the whole overridable vocabulary', () => {
  it('renders exactly the five ruled knobs (OVERRIDABLE_POLICY_KEYS)', () => {
    expect(POLICY_KNOBS.map((k) => k.key)).toEqual(
      ['attended', 'autopilot', 'max_cycles', 'idle_secs', 'success_criteria'],
    )
    render(<PolicyOverridesPanel runId="r1" initial={{}} />)
    for (const { label } of POLICY_KNOBS) expect(screen.getByText(label)).toBeTruthy()
  })
})

describe('sparse is shown honestly', () => {
  it('an untouched overlay shows every knob as the kind default, with no clear affordances', () => {
    render(<PolicyOverridesPanel runId="r1" initial={{}} />)
    expect(screen.getAllByText('Kind default')).toHaveLength(POLICY_KNOBS.length)
    expect(screen.queryByText('Clear override')).toBeNull()
    expect(screen.queryByText('Clear all')).toBeNull()
  })

  it('a set knob shows its value and its own clear affordance; the rest stay defaults', () => {
    render(<PolicyOverridesPanel runId="r1" initial={{ max_cycles: 5 }} />)
    expect(screen.getByLabelText('Max cycles override')).toHaveProperty('value', '5')
    expect(screen.getAllByText('Kind default')).toHaveLength(POLICY_KNOBS.length - 1)
    expect(screen.getAllByText('Clear override')).toHaveLength(1)
    expect(screen.getByText('Clear all')).toBeTruthy()
  })
})

describe('every write is a REPLACE of the whole overlay', () => {
  it('overriding a knob PUTs the existing overlay plus the new key', async () => {
    render(<PolicyOverridesPanel runId="r1" initial={{ max_cycles: 5 }} />)
    fireEvent.click(screen.getByTitle('Override Attended for this run only'))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0]).toEqual({ id: 'r1', overrides: { max_cycles: 5, attended: true } })
  })

  it('clearing one knob PUTs the overlay WITHOUT the key — never `key: null`', async () => {
    render(<PolicyOverridesPanel runId="r1" initial={{ max_cycles: 5, attended: true }} />)
    fireEvent.click(
      screen.getByTitle('Clear the Max cycles override — this run falls back to the kind default'),
    )
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].overrides).toEqual({ attended: true })
    expect('max_cycles' in calls[0].overrides).toBe(false)
  })

  it('"Clear all" PUTs `{}` — the store contract for clearing every override', async () => {
    render(<PolicyOverridesPanel runId="r1" initial={{ max_cycles: 5, attended: true }} />)
    fireEvent.click(screen.getByText('Clear all'))
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].overrides).toEqual({})
    await waitFor(() =>
      expect(screen.getAllByText('Kind default')).toHaveLength(POLICY_KNOBS.length))
  })

  it('what renders after a write is what the SERVER persisted, not what was sent', async () => {
    respond = () => ({})
    render(<PolicyOverridesPanel runId="r1" initial={{}} />)
    fireEvent.click(screen.getByTitle('Override Autopilot for this run only'))
    await waitFor(() => expect(calls).toHaveLength(1))
    await waitFor(() =>
      expect(screen.getAllByText('Kind default')).toHaveLength(POLICY_KNOBS.length))
  })

  it('the free-text knob commits on blur, not per keystroke', async () => {
    render(<PolicyOverridesPanel runId="r1" initial={{ success_criteria: '' }} />)
    const input = screen.getByLabelText('Success criteria override')
    fireEvent.change(input, { target: { value: 'PR opened' } })
    expect(calls).toHaveLength(0)
    fireEvent.blur(input)
    await waitFor(() => expect(calls).toHaveLength(1))
    expect(calls[0].overrides).toEqual({ success_criteria: 'PR opened' })
  })
})

describe('the editor exists only where the backend would accept the write', () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), "src", rel), 'utf8')

  it('the prelaunch vocabulary mirrors the backend phase map (draft, today)', () => {
    expect([...PRELAUNCH_RUN_STATUSES]).toEqual(['draft'])
    expect(isPrelaunch('draft')).toBe(true)
    for (const s of ['running', 'paused', 'needs_input', 'complete', 'failed', 'cancelled', 'escalated']) {
      expect(isPrelaunch(s), s).toBe(false)
    }
  })

  it('the run view mounts the panel behind the phase gate, not a status literal', () => {
    const code = read('features/workflows/WorkflowRunDetail.tsx')
    expect(code).toMatch(/\{isPrelaunch\(run\.status\) && \(\s*\n\s*<PolicyOverridesPanel/)
    expect(code, 'no draft literal — the phase set owns the vocabulary')
      .not.toMatch(/run\.status === 'draft'/)
  })
})
