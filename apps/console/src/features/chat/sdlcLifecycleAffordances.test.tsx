import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { api, type Loop } from '../../shared/data/api'
import { SdlcProgressCard, type SdlcRef } from './SdlcProgressCard'


const REF: SdlcRef = { kind: 'loop', id: 'abc123', created: false }

const NAME = 'Ship the widget'

const loopIn = (status: string): Loop => ({
  id: REF.id, kind: 'goal', name: NAME, task: NAME,
  execution: 'solo', agent: 'claude', model: 'sonnet', attended: true,
  max_cycles: 10, idle_secs: 60, success_criteria: null,
  status, total_cycles: 0, error_message: null,
  created_at: 0, started_at: null, completed_at: null,
} as unknown as Loop)

async function mountIn(status: string) {
  vi.spyOn(api, 'uLoop').mockResolvedValue(loopIn(status))
  render(<SdlcProgressCard refObj={REF} controllable />)
  await waitFor(() => expect(screen.getByText(NAME), 'the card must have rendered this loop').toBeTruthy())
}

const control = (name: string) => screen.queryByRole('button', { name })

afterEach(() => vi.restoreAllMocks())

describe('the in-chat SDLC card offers exactly the lifecycle actions the backend accepts', () => {
  it('a BLOCKED loop can be resumed — the state that could be resumed from nowhere', async () => {
    await mountIn('blocked')
    expect(control('Resume'), 'the backend accepts resume from blocked').not.toBeNull()
  })

  it('a FAILED loop can be resumed — the state whose answer differed per surface', async () => {
    await mountIn('failed')
    expect(control('Resume'), 'the backend accepts resume from failed').not.toBeNull()
  })

  it.each(['paused', 'stagnant', 'needs_input'])('a %s loop can still be resumed', async (status) => {
    await mountIn(status)
    expect(control('Resume')).not.toBeNull()
  })

  it('a RUNNING loop is paused, not resumed', async () => {
    await mountIn('running')
    expect(control('Pause'), 'pause is accepted only from running').not.toBeNull()
    expect(control('Resume'), 'the backend refuses resume from running').toBeNull()
  })

  it('a PAUSED loop is resumed, not paused', async () => {
    await mountIn('paused')
    expect(control('Pause'), 'the backend refuses pause from anything but running').toBeNull()
  })

  it('a COMPLETE loop offers no lifecycle action at all', async () => {
    await mountIn('complete')
    expect(control('Resume'), 'a finished loop is not resumable').toBeNull()
    expect(control('Pause')).toBeNull()
    expect(control('Stop'), 'stop is refused once the loop is no longer active').toBeNull()
  })

  it('stop follows the active set, so it reaches BLOCKED', async () => {
    await mountIn('blocked')
    expect(control('Stop'), 'blocked is an active status').not.toBeNull()
  })

  it('…and stops short of FAILED, which is resumable without being active', async () => {
    await mountIn('failed')
    expect(control('Stop'), 'the backend refuses stop from failed — it 409s').toBeNull()
  })
})
