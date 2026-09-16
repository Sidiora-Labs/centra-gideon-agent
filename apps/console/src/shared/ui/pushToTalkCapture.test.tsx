import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, act, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Composer } from './Composer'



vi.mock('../data/api', () => ({
  api: {
    gideonConfig: vi.fn(async () => ({ voice: { push_to_talk_chord: 'Alt+F13' } })),
    dashboardConfig: vi.fn(async () => ({ send_on_enter: true })),
  },
}))

function fakeTrack() {
  return { kind: 'audio', readyState: 'live', stop: vi.fn(), addEventListener: vi.fn() }
}

let tracks: ReturnType<typeof fakeTrack>[] = []
let recorders: FakeRecorder[] = []

class FakeRecorder {
  state = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  tail = 'tail-audio'
  constructor(public stream: { getTracks: () => ReturnType<typeof fakeTrack>[] }) {
    recorders.push(this)
  }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob([this.tail], { type: 'audio/webm' }) })
    this.onstop?.()
  }
}

function installBridge() {
  let push: ((p: { action: string; reason?: string }) => void) | null = null
  const bridge = {
    capabilities: {
      names: () => ['audio_capture'],
      probe: vi.fn(async () => ({ available: true, granted: 'granted', requestable: false, reason: '' })),
      snapshot: vi.fn(async () => ({})),
      request: vi.fn(async () => ({ granted: true, state: 'granted', prompted: true, reason: '' })),
      on: vi.fn(() => () => {}),
    },
    pushToTalk: {
      bind: vi.fn(async (chord: string) => ({ ok: true, chord, conflict: false, reason: '' })),
      setCapturing: vi.fn(async () => true),
      on: (cb: (p: { action: string; reason?: string }) => void) => { push = cb; return () => { push = null } },
    },
  }
  ;(window as unknown as { gideonDesktop: unknown }).gideonDesktop = bridge
  return { bridge, fire: (p: { action: string; reason?: string }) => push?.(p) }
}

beforeEach(() => {
  tracks = []
  recorders = []
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }),
  })
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: {
      getUserMedia: vi.fn(async () => {
        const t = [fakeTrack(), fakeTrack()]
        tracks.push(...t)
        return { getTracks: () => t, getAudioTracks: () => t }
      }),
    },
  })
  ;(globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder
})

afterEach(() => {
  cleanup()
  delete (window as unknown as { gideonDesktop?: unknown }).gideonDesktop
  vi.clearAllMocks()
})

function mountComposer(opts: { value?: string; transcript?: string } = {}) {
  const state = { value: opts.value ?? '' }
  const uploaded: Blob[] = []
  const errors: string[] = []
  const onTranscribe = vi.fn(async (blob: Blob) => {
    uploaded.push(blob)
    return opts.transcript ?? 'hello there'
  })
  const view = render(
    <Composer
      value={state.value}
      onChange={(v) => { state.value = v }}
      onSend={() => {}}
      onTranscribe={onTranscribe}
      onMicError={(m) => errors.push(m)}
    />,
  )
  return { state, uploaded, errors, onTranscribe, view }
}

async function chord(fire: (p: { action: string }) => void) {
  await act(async () => { fire({ action: 'toggle' }) })
}


describe('capture stops with the gesture', () => {
  it('a second chord press stops EVERY track it opened', async () => {
    const { fire } = installBridge()
    mountComposer()

    await chord(fire)
    await waitFor(() => expect(tracks.length).toBe(2))
    expect(tracks.every((t) => t.stop.mock.calls.length === 0)).toBe(true)

    await chord(fire)

    await waitFor(() => {
      for (const t of tracks) expect(t.stop).toHaveBeenCalled()
    })
  })

  it('the shell’s runaway-capture stop also releases the tracks', async () => {
    const { fire } = installBridge()
    mountComposer()
    await chord(fire)
    await waitFor(() => expect(tracks.length).toBe(2))

    await act(async () => { fire({ action: 'stop', reason: 'capture-timeout' }) })

    await waitFor(() => {
      for (const t of tracks) expect(t.stop).toHaveBeenCalled()
    })
  })

  it('clicking the indicator stops the tracks too', async () => {
    const { fire } = installBridge()
    mountComposer()
    await chord(fire)
    const chip = await screen.findByRole('button', { name: /listening to your microphone/i })

    await act(async () => { chip.click() })

    await waitFor(() => {
      for (const t of tracks) expect(t.stop).toHaveBeenCalled()
    })
  })

  it('a press does not open a SECOND stream while one is live', async () => {
    const { fire } = installBridge()
    mountComposer()
    await chord(fire)
    await waitFor(() => expect(recorders.length).toBe(1))
    await chord(fire)
    await waitFor(() => expect(tracks[0].stop).toHaveBeenCalled())
    expect(recorders.length).toBe(1)
  })
})


describe('the capturing indicator', () => {
  it('is absent at rest and present for as long as capture is live', async () => {
    const { fire } = installBridge()
    mountComposer()
    expect(screen.queryByRole('button', { name: /listening to your microphone/i })).toBeNull()

    await chord(fire)
    expect(await screen.findByRole('button', { name: /listening to your microphone/i })).toBeTruthy()

    await chord(fire)
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /listening to your microphone/i })).toBeNull())
  })

  it('does not depend on its animation', async () => {
    const { fire } = installBridge()
    mountComposer()
    await chord(fire)
    const chip = await screen.findByRole('button', { name: /listening to your microphone/i })

    chip.querySelectorAll('.status-pulse').forEach((n) => n.remove())
    expect(chip.getAttribute('aria-label')).toMatch(/listening/i)
    expect(chip.textContent).toMatch(/Listening/)
    expect(chip.isConnected).toBe(true)
  })

  it('reports the live state to the shell, which is what lights the menu bar', async () => {
    const { bridge, fire } = installBridge()
    mountComposer()
    await chord(fire)
    await waitFor(() => expect(bridge.pushToTalk.setCapturing).toHaveBeenCalledWith(true))
    await chord(fire)
    await waitFor(() => expect(bridge.pushToTalk.setCapturing).toHaveBeenCalledWith(false))
  })

  it('takes the shell indicator down when the composer unmounts mid-capture', async () => {
    const { bridge, fire } = installBridge()
    const { view } = mountComposer()
    await chord(fire)
    await waitFor(() => expect(bridge.pushToTalk.setCapturing).toHaveBeenCalledWith(true))

    bridge.pushToTalk.setCapturing.mockClear()
    await act(async () => { view.unmount() })

    expect(bridge.pushToTalk.setCapturing).toHaveBeenCalledWith(false)
  })
})


describe('the transcript', () => {
  it('includes the audio flushed as the key came up', async () => {
    const { fire } = installBridge()
    const { uploaded } = mountComposer()

    await chord(fire)
    await waitFor(() => expect(recorders.length).toBe(1))
    act(() => { recorders[0].ondataavailable?.({ data: new Blob(['body-audio'], { type: 'audio/webm' }) }) })
    await chord(fire)

    await waitFor(() => expect(uploaded.length).toBe(1))
    const text = await uploaded[0].text()
    expect(text).toContain('body-audio')
    expect(text).toContain('tail-audio')
  })

  it('lands at the cursor rather than being appended, and keeps the draft', async () => {
    const { fire } = installBridge()
    const { state } = mountComposer({ value: 'draft ', transcript: 'spoken words' })

    await chord(fire)
    await waitFor(() => expect(recorders.length).toBe(1))
    await chord(fire)

    await waitFor(() => expect(state.value).toContain('spoken words'))
    expect(state.value).toContain('draft ')
    expect(state.value).toBe('spoken wordsdraft ')
    expect(state.value).not.toBe('draft spoken words')
  })

  it('routes the insertion through the composer’s caret API, not string concatenation', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/ui/Composer.tsx"), 'utf8')
    expect(src).toMatch(/insertAtCaret\(text\)/)
    expect(src).not.toMatch(/onChange\(value \+ text\)/)
  })
})


describe('a denied microphone degrades with something actionable', () => {
  it('says where to turn it on, and opens no stream', async () => {
    const { bridge, fire } = installBridge()
    bridge.capabilities.probe = vi.fn(async () => ({
      available: true, granted: 'denied', requestable: false, reason: '',
    }))
    const { errors } = mountComposer()

    await chord(fire)

    await waitFor(() => expect(errors.length).toBe(1))
    expect(errors[0]).toMatch(/System Settings/i)
    expect(errors[0]).toMatch(/Privacy/i)
    expect(tracks.length).toBe(0)
    expect(screen.queryByRole('button', { name: /listening to your microphone/i })).toBeNull()
  })

  it('a not-determined grant is requested through the bridge (the TCC leg)', async () => {
    const { bridge, fire } = installBridge()
    bridge.capabilities.probe = vi.fn(async () => ({
      available: true, granted: 'not-determined', requestable: true, reason: '',
    }))
    const { errors } = mountComposer()

    await chord(fire)

    await waitFor(() => expect(bridge.capabilities.request).toHaveBeenCalledWith('audio_capture'))
    await waitFor(() => expect(tracks.length).toBe(2))
    expect(errors).toEqual([])
  })
})


describe('the chord is bound from config', () => {
  it('binds the configured chord, not the default', async () => {
    const { bridge } = installBridge()
    mountComposer()
    await waitFor(() => expect(bridge.pushToTalk.bind).toHaveBeenCalledWith('Alt+F13'))
  })

  it('binds nothing in a browser tab', async () => {
    const { errors } = mountComposer()
    await new Promise((r) => setTimeout(r, 10))
    expect(errors).toEqual([])
    expect(screen.queryByRole('button', { name: /listening to your microphone/i })).toBeNull()
  })
})
