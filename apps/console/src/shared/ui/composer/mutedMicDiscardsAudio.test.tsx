import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act, cleanup } from '@testing-library/react'
import { Composer } from '../Composer'
import { HANDS_FREE_SEGMENT_MS } from './micCaptureSession'

vi.mock('../../data/api', () => ({
  api: {
    gideonConfig: vi.fn(async () => ({ voice: {} })),
    dashboardConfig: vi.fn(async () => ({ send_on_enter: true })),
  },
}))

interface FakeTrack {
  kind: string
  readyState: string
  stop: ReturnType<typeof vi.fn>
  addEventListener: ReturnType<typeof vi.fn>
}

function fakeTrack(): FakeTrack {
  const track: FakeTrack = {
    kind: 'audio',
    readyState: 'live',
    stop: vi.fn(() => { track.readyState = 'ended' }),
    addEventListener: vi.fn(),
  }
  return track
}

class FakeRecorder {
  state = 'inactive'
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(public stream: { getTracks: () => FakeTrack[] }) { recorders.push(this) }
  start() { this.state = 'recording' }
  stop() {
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['final-flush'], { type: 'audio/webm' }) })
    this.onstop?.()
  }
  speak(words: string) { this.ondataavailable?.({ data: new Blob([words], { type: 'audio/webm' }) }) }
}

let tracks: FakeTrack[] = []
let recorders: FakeRecorder[] = []
let openMic: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  tracks = []
  recorders = []
  openMic = vi.fn(async () => {
    const opened = [fakeTrack(), fakeTrack()]
    tracks.push(...opened)
    return { getTracks: () => opened, getAudioTracks: () => opened }
  })
  Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia: openMic } })
  ;(globalThis as unknown as { MediaRecorder: unknown }).MediaRecorder = FakeRecorder
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.clearAllMocks()
})

const settle = async (rounds = 4) => {
  for (let round = 0; round < rounds; round++) await act(async () => { await Promise.resolve() })
}

function mountVoiceComposer(transcript = 'water the plants go ahead') {
  const transcribed: { blob: Blob; duplex?: boolean }[] = []
  const submitted: string[] = []
  const errors: string[] = []
  const drafts: string[] = []
  const onTranscribe = vi.fn(async (blob: Blob, options?: { duplex?: boolean }) => {
    transcribed.push({ blob, duplex: options?.duplex })
    return transcript
  })
  const Host = ({ speaking }: { speaking: boolean }) => (
    <Composer
      value=""
      onChange={(next) => { drafts.push(next) }}
      onSend={() => {}}
      onTranscribe={onTranscribe}
      onMicError={(message) => errors.push(message)}
      onHandsFreeSubmit={(text) => submitted.push(text)}
      handsFree={{
        confirmationPhrases: ['go ahead'],
        exitPhrases: ['never mind'],
        speaking,
        muteWhileSpeaking: true,
      }}
    />
  )
  const view = render(<Host speaking={false} />)
  const playback = async (speaking: boolean) => {
    view.rerender(<Host speaking={speaking} />)
    await settle()
  }
  return { view, playback, transcribed, submitted, errors, drafts, onTranscribe }
}

async function startHandsFree() {
  await act(async () => { screen.getByRole('button', { name: 'Hands-free voice' }).click() })
  await settle()
}

const capturing = () => screen.queryByRole('button', { name: /listening to your microphone/i })
const earLabel = () => screen.getByRole('button', { name: /^Hands-free voice/ }).getAttribute('aria-label') ?? ''

async function advance(ms: number) {
  await act(async () => { vi.advanceTimersByTime(ms) })
  await settle()
}


describe('muting while Gideon speaks throws the microphone away', () => {
  it('drops the buffered and the final audio, releases the tracks, and asks for no transcript', async () => {
    const voice = mountVoiceComposer()
    await startHandsFree()

    expect(recorders).toHaveLength(1)
    expect(recorders[0].state).toBe('recording')
    expect(tracks).toHaveLength(2)
    expect(tracks.every((track) => track.readyState === 'live')).toBe(true)
    expect(capturing()).toBeTruthy()

    act(() => { recorders[0].speak('private-speech-while-listening') })

    await voice.playback(true)

    expect(recorders[0].state, 'the recorder must be stopped, not left running').toBe('inactive')
    expect(recorders[0].ondataavailable, 'a live data sink would keep buffering private audio').toBeNull()
    expect(recorders[0].onstop, 'a live stop handler would transcribe the final flush').toBeNull()
    for (const track of tracks) {
      expect(track.stop).toHaveBeenCalled()
      expect(track.readyState).toBe('ended')
    }
    expect(voice.onTranscribe, 'muted audio must never reach the transcription endpoint').not.toHaveBeenCalled()
    expect(voice.transcribed).toEqual([])
    expect(voice.submitted).toEqual([])
    expect(voice.errors).toEqual([])
    expect(capturing(), 'the capture chip must go down with the stream').toBeNull()
    expect(earLabel()).toMatch(/paused while speaking/)

    act(() => { recorders[0].stop() })
    await settle()
    expect(voice.onTranscribe).not.toHaveBeenCalled()
  })

  it('does not reopen the microphone or transcribe when the segment timer would have fired', async () => {
    const voice = mountVoiceComposer()
    await startHandsFree()
    act(() => { recorders[0].speak('private-speech-while-listening') })

    await voice.playback(true)
    expect(openMic).toHaveBeenCalledTimes(1)

    await advance(HANDS_FREE_SEGMENT_MS * 3)

    expect(recorders, 'a muted session must not start a new recording').toHaveLength(1)
    expect(openMic, 'a muted session must not reopen the microphone').toHaveBeenCalledTimes(1)
    expect(voice.onTranscribe).not.toHaveBeenCalled()
    expect(capturing()).toBeNull()
  })

  it('keeps nothing from the muted stretch when playback ends and listening resumes', async () => {
    const voice = mountVoiceComposer()
    await startHandsFree()
    act(() => { recorders[0].speak('private-speech-while-listening') })
    await voice.playback(true)

    await voice.playback(false)

    expect(recorders, 'listening resumes on a fresh recorder').toHaveLength(2)
    expect(openMic).toHaveBeenCalledTimes(2)
    act(() => { recorders[1].speak('after-playback-speech') })
    await advance(HANDS_FREE_SEGMENT_MS)

    expect(voice.transcribed).toHaveLength(1)
    const audio = await voice.transcribed[0].blob.text()
    expect(audio).toContain('after-playback-speech')
    expect(audio, 'the pre-mute buffer must not survive the mute').not.toContain('private-speech-while-listening')
  })
})


describe('the unmuted control — the same flow does reach transcription', () => {
  it('holds the segment open for exactly HANDS_FREE_SEGMENT_MS, then sends buffered and final audio', async () => {
    const voice = mountVoiceComposer()
    await startHandsFree()
    act(() => { recorders[0].speak('spoken-body') })

    await advance(HANDS_FREE_SEGMENT_MS - 1)
    expect(recorders[0].state, 'the segment closed early').toBe('recording')
    expect(voice.onTranscribe).not.toHaveBeenCalled()

    await advance(1)

    expect(recorders[0].state).toBe('inactive')
    expect(voice.transcribed).toHaveLength(1)
    expect(voice.transcribed[0].duplex, 'a hands-free segment is a duplex transcript').toBe(true)
    const audio = await voice.transcribed[0].blob.text()
    expect(audio).toContain('spoken-body')
    expect(audio).toContain('final-flush')
    expect(voice.submitted).toEqual(['water the plants'])
    for (const track of tracks.slice(0, 2)) expect(track.stop).toHaveBeenCalled()
    expect(recorders.length, 'hands-free keeps listening after a segment').toBe(2)
    expect(voice.errors).toEqual([])
  })

  it('never opens the microphone at all when playback is already under way', async () => {
    const voice = mountVoiceComposer()
    await voice.playback(true)

    await startHandsFree()
    await advance(HANDS_FREE_SEGMENT_MS * 2)

    expect(openMic).not.toHaveBeenCalled()
    expect(recorders).toEqual([])
    expect(voice.onTranscribe).not.toHaveBeenCalled()
    expect(earLabel()).toMatch(/paused while speaking/)
  })
})
