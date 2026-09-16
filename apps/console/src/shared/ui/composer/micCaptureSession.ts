import { accumulateTranscript } from './duplex'
import { stopStream } from './displayCapture'
import { MediaOwnership, recordedAudio } from './mediaOwnership'

export type MicState = 'idle' | 'recording' | 'transcribing'
export const HANDS_FREE_SEGMENT_MS = 4000

export interface HandsFreeOptions {
  enabled: boolean
  confirmationPhrases: readonly string[]
  exitPhrases: readonly string[]
  onSubmit: (text: string) => void
  muted?: boolean
  onBuffer?: (text: string) => void
}

export interface MicCallbacks {
  onTranscribe?: (blob: Blob, opts?: { duplex?: boolean }) => Promise<string>
  onText?: (text: string) => void
  onError?: (message: string) => void
  handsFree?: HandsFreeOptions
}

interface Recording {
  ticket: number
  recorder: MediaRecorder
  stream: MediaStream
  chunks: Blob[]
}

export class MicCaptureSession {
  callbacks: MicCallbacks = {}
  private ownership = new MediaOwnership()
  private listeners = new Set<() => void>()
  private snapshot: { state: MicState; listening: boolean } = { state: 'idle', listening: false }
  private recording: Recording | null = null
  private segment: ReturnType<typeof setTimeout> | undefined
  private mounted = false
  private handsFree = false
  private buffer = ''

  getSnapshot = () => this.snapshot
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  private publish(state: MicState, listening = this.snapshot.listening) {
    if (state === this.snapshot.state && listening === this.snapshot.listening) return
    this.snapshot = { state, listening }
    this.listeners.forEach(listener => listener())
  }

  activate = () => {
    this.mounted = true
    this.configure()
  }

  dispose = () => {
    this.mounted = false
    this.handsFree = false
    this.buffer = ''
    this.drain()
    this.publish('idle', false)
  }

  configure = () => {
    const enabled = !!this.callbacks.handsFree?.enabled
    if (this.handsFree !== enabled) {
      this.handsFree = enabled
      this.publish(this.snapshot.state, enabled)
      if (!enabled) {
        this.buffer = ''
        this.drain()
      } else if (this.recording) this.scheduleSegment()
    }
    if (!enabled) return
    if (this.callbacks.handsFree?.muted) this.drain()
    else this.continueListening()
  }

  private clearTimer() {
    if (this.segment !== undefined) clearTimeout(this.segment)
    this.segment = undefined
  }

  private scheduleSegment() {
    this.clearTimer()
    this.segment = setTimeout(() => this.stop(), HANDS_FREE_SEGMENT_MS)
  }

  private release(recording: Recording) {
    recording.recorder.ondataavailable = null
    recording.recorder.onstop = null
    recording.recorder.onerror = null
    if (recording.recorder.state !== 'inactive') recording.recorder.stop()
    stopStream(recording.stream)
    if (this.recording === recording) this.recording = null
  }

  drain = () => {
    this.ownership.cancel()
    this.clearTimer()
    if (this.recording) this.release(this.recording)
    this.publish('idle')
  }

  private stop() {
    this.clearTimer()
    const recorder = this.recording?.recorder
    if (recorder && recorder.state !== 'inactive') recorder.stop()
  }

  toggle = () => {
    if (this.snapshot.state === 'recording') {
      this.publish('recording', false)
      this.stop()
    } else if (this.snapshot.state === 'idle') {
      if (this.ownership.busy) {
        this.publish('idle', false)
        this.drain()
      } else void this.start()
    }
  }

  private continueListening() {
    if (this.snapshot.listening && this.handsFree && !this.callbacks.handsFree?.muted) void this.start()
  }

  private async start() {
    if (!this.mounted || this.ownership.busy || !this.callbacks.onTranscribe) return
    if (this.handsFree && this.callbacks.handsFree?.muted) return
    const ticket = this.ownership.begin()
    let stream: MediaStream | undefined
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!this.ownership.owns(ticket)) { stopStream(stream); return }
      const recorder = new MediaRecorder(stream)
      const recording: Recording = { ticket, recorder, stream, chunks: [] }
      this.recording = recording
      recorder.ondataavailable = event => {
        if (this.ownership.owns(ticket) && event.data.size) recording.chunks.push(event.data)
      }
      recorder.onstop = () => { void this.transcribe(recording) }
      recorder.onerror = () => {
        if (!this.ownership.owns(ticket)) return
        this.drain()
        this.publish('idle', false)
        this.callbacks.onError?.('No microphone available for voice input.')
      }
      recorder.start()
      this.publish('recording')
      if (this.handsFree) this.scheduleSegment()
    } catch (error) {
      if (!this.ownership.owns(ticket)) { stopStream(stream); return }
      if (!this.recording) stopStream(stream)
      this.drain()
      this.publish('idle', false)
      const denied = ['NotAllowedError', 'SecurityError'].includes((error as Error)?.name)
      this.callbacks.onError?.(denied
        ? 'Microphone access was blocked — allow it in your browser to use voice input.'
        : 'No microphone available for voice input.')
    }
  }

  private async transcribe(recording: Recording) {
    this.clearTimer()
    this.release(recording)
    const { ticket } = recording
    if (!this.ownership.owns(ticket)) return
    const audio = recordedAudio(recording.chunks)
    try {
      if (!audio.size) return
      this.publish('transcribing')
      const text = await this.callbacks.onTranscribe?.(audio, { duplex: this.handsFree })
      if (!this.ownership.owns(ticket)) return
      const options = this.callbacks.handsFree
      if (options?.enabled) {
        const step = accumulateTranscript(this.buffer, text || '', {
          confirmation: options.confirmationPhrases, exit: options.exitPhrases,
        })
        this.buffer = step.action === 'submit' ? '' : step.buffer
        options.onBuffer?.(this.buffer)
        if (step.action === 'submit') options.onSubmit(step.buffer)
      } else if (text) this.callbacks.onText?.(text)
    } catch (error) {
      if (!this.ownership.owns(ticket)) return
      const message = (error as Error)?.message
      this.callbacks.onError?.(message
        ? `Couldn't transcribe the audio: ${message}`
        : 'Couldn’t transcribe the audio — try again.')
    } finally {
      if (this.ownership.finish(ticket)) {
        this.publish('idle')
        this.continueListening()
      }
    }
  }
}
