import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, fireEvent, waitFor } from '@testing-library/react'
import type { VoiceProfile, VoiceResolution } from '../../shared/data/api'


const voiceProfiles = vi.fn()
const voiceResolve = vi.fn()
const voiceMigrate = vi.fn()
const voiceBindingSet = vi.fn()
const voiceProfileLock = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    voiceProfiles: (...a: unknown[]) => voiceProfiles(...a),
    voiceResolve: (...a: unknown[]) => voiceResolve(...a),
    voiceMigrate: (...a: unknown[]) => voiceMigrate(...a),
    voiceBindingSet: (...a: unknown[]) => voiceBindingSet(...a),
    voiceProfileLock: (...a: unknown[]) => voiceProfileLock(...a),
    voiceProfileUnlock: vi.fn(),
    voiceProfileDelete: vi.fn(),
    voiceBindingClear: vi.fn(),
    voiceProfileCreate: vi.fn(),
  },
}))

const { VoiceProfilesSection } = await import('./VoiceProfilesSection')

function profile(over: Partial<VoiceProfile> = {}): VoiceProfile {
  return {
    id: 'vp-a1b2c3d4', name: 'Reading voice', kind: 'design', provider: 'piper', model: 'en_US-amy',
    ref_audio: '', ref_text: '', design_params: {}, instruct: '',
    seed: 0, language: '', speed: 1, locked: false, locked_at: '',
    verified_own_voice: false, consent_text: '', consent_audio: '', consent_recorded_at: '',
    history: [], created_at: '', updated_at: '',
    artifacts: {}, history_count: 0,
    ...over,
  }
}

function resolution(over: Partial<VoiceResolution> = {}): VoiceResolution {
  return { surface: '', resolved: true, level: 'built-in', ...over }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  voiceResolve.mockResolvedValue(resolution())
})

describe('the effective voice comes from the resolver, never from a client-side chain', () => {
  it('states the level the SERVER reports even when the binding map suggests another', async () => {
    voiceProfiles.mockResolvedValue({
      profiles: [profile()],
      bindings: { 'channel:general': 'vp-a1b2c3d4' },
    })
    voiceResolve.mockImplementation(async (surface: string) =>
      surface === 'channel:general'
        ? resolution({ surface, level: 'default' })
        : resolution({ surface }))

    const { findByText, queryByText } = render(<VoiceProfilesSection />)

    expect(await findByText('default voice')).toBeTruthy()
    expect(queryByText('this binding')).toBeNull()
  })

  it('surfaces a failed resolver read instead of falling through to the built-in voice', async () => {
    voiceProfiles.mockResolvedValue({
      profiles: [profile()],
      bindings: { 'agent:writer': 'vp-a1b2c3d4' },
    })
    voiceResolve.mockRejectedValue(new Error('boom'))

    const { findByRole, getByText, queryByText } = render(<VoiceProfilesSection />)

    const alert = await findByRole('alert')
    expect(alert.textContent).toContain('Could not read which voice wins')
    expect(queryByText('built-in voice')).toBeNull()
    expect(getByText('Voice profiles')).toBeTruthy()
    expect(getByText('vp-a1b2c3d4')).toBeTruthy()
  })
})

describe('consent is provenance, not permission', () => {
  it('renders a consent-less profile as plain absence, with no error', async () => {
    voiceProfiles.mockResolvedValue({ profiles: [profile()], bindings: {} })

    const { findByText, queryByRole } = render(<VoiceProfilesSection />)

    expect(await findByText('No consent recorded')).toBeTruthy()
    expect(queryByRole('alert')).toBeNull()
  })

  it('shows a verified profile as verified', async () => {
    voiceProfiles.mockResolvedValue({
      profiles: [profile({ verified_own_voice: true })],
      bindings: {},
    })
    const { findByText } = render(<VoiceProfilesSection />)
    expect(await findByText('own voice verified')).toBeTruthy()
  })
})

describe('a bind that warns still succeeded', () => {
  it('surfaces the warning as a status, not as a failure', async () => {
    voiceProfiles.mockResolvedValue({
      profiles: [profile({ kind: 'clone', artifacts: { ref_audio: true } })],
      bindings: {},
    })
    voiceBindingSet.mockResolvedValue({
      bindings: { 'agent:writer': 'vp-a1b2c3d4' },
      warning: 'unverified_clone_consent',
    })

    const { findByLabelText, getByText, findByRole, queryByRole } = render(<VoiceProfilesSection />)

    fireEvent.change(await findByLabelText('Surface kind'), { target: { value: 'agent' } })
    fireEvent.change(await findByLabelText('Surface name'), { target: { value: 'writer' } })
    fireEvent.click(getByText('Bind'))

    const status = await findByRole('status')
    expect(status.textContent).toContain('no consent on record')
    expect(status.textContent).not.toContain('unverified_clone_consent')
    expect(queryByRole('alert')).toBeNull()
    expect(voiceBindingSet).toHaveBeenCalledWith('agent:writer', 'vp-a1b2c3d4')
  })

  it('still says something when the reason code is one it does not know', async () => {
    voiceProfiles.mockResolvedValue({ profiles: [profile()], bindings: {} })
    voiceBindingSet.mockResolvedValue({ bindings: {}, warning: 'some_future_reason' })

    const { findByText, findByRole } = render(<VoiceProfilesSection />)
    fireEvent.click(await findByText('Bind'))

    const status = await findByRole('status')
    expect(status.textContent).toContain('some_future_reason')
  })
})

describe('migration happens only on an explicit click (§6)', () => {
  it('never calls migrate while merely rendering the empty state', async () => {
    voiceProfiles.mockResolvedValue({ profiles: [], bindings: {} })

    const { findByText } = render(<VoiceProfilesSection />)
    await findByText('Migrate current voice')

    expect(voiceMigrate).not.toHaveBeenCalled()
  })

  it('calls migrate when the button is pressed', async () => {
    voiceProfiles.mockResolvedValue({ profiles: [], bindings: {} })
    voiceMigrate.mockResolvedValue(profile())

    const { findByText } = render(<VoiceProfilesSection />)
    fireEvent.click(await findByText('Migrate current voice'))

    await waitFor(() => expect(voiceMigrate).toHaveBeenCalledTimes(1))
  })
})

describe('the unbound state reads as an answer, not as a blank', () => {
  it('states that the built-in voice speaks everywhere when nothing is bound', async () => {
    voiceProfiles.mockResolvedValue({ profiles: [profile()], bindings: {} })

    const { findByText } = render(<VoiceProfilesSection />)

    expect(await findByText('Everywhere')).toBeTruthy()
    expect(await findByText('built-in voice')).toBeTruthy()
  })
})

describe('locking pins a generation the user already heard', () => {
  it('gates lock WITH ITS REASON when the profile has no history', async () => {
    voiceProfiles.mockResolvedValue({ profiles: [profile({ history_count: 0 })], bindings: {} })

    const { findByLabelText } = render(<VoiceProfilesSection />)
    const btn = await findByLabelText('Lock Reading voice to its latest generation')

    expect(btn.getAttribute('aria-disabled')).toBe('true')
    expect(btn.getAttribute('title')).toContain('locking pins a generation you already heard')

    fireEvent.click(btn)
    expect(voiceProfileLock).not.toHaveBeenCalled()
  })

  it('locks the newest generation by index when history exists', async () => {
    voiceProfiles.mockResolvedValue({ profiles: [profile({ history_count: 3 })], bindings: {} })
    voiceProfileLock.mockResolvedValue(profile({ locked: true }))

    const { findByLabelText } = render(<VoiceProfilesSection />)
    fireEvent.click(await findByLabelText('Lock Reading voice to its latest generation'))

    await waitFor(() => expect(voiceProfileLock).toHaveBeenCalledWith('vp-a1b2c3d4', 2))
  })
})
