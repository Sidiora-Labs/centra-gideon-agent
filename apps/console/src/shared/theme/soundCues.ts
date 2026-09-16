
import { prefersReducedMotion } from './motion'

export type CuePoint = 'turn_complete' | 'approval_needed' | 'error'

export type CueName = CuePoint | 'coin_blip' | 'terminal_bell'

export interface CueRecipe {
  wave: OscillatorType
  freqs: number[]
  durMs: number
  gain: number
}

export const MAX_GAIN = 0.1

export const CUES: Record<CueName, CueRecipe> = {
  turn_complete: { wave: 'sine', freqs: [659.25, 830.61], durMs: 130, gain: 0.05 },
  approval_needed: { wave: 'triangle', freqs: [880, 1108.73, 880], durMs: 210, gain: 0.06 },
  error: { wave: 'square', freqs: [311.13, 233.08], durMs: 170, gain: 0.04 },
  // A cabinet swallowing a coin: a bright rising fifth on a square wave, the 8-bit
  // "credit accepted". gideon-arcade re-voices `turn_complete` with it.
  coin_blip: { wave: 'square', freqs: [987.77, 1479.98], durMs: 110, gain: 0.05 },
  terminal_bell: { wave: 'triangle', freqs: [1760], durMs: 90, gain: 0.05 },
}

let voices: Partial<Record<CuePoint, CueName>> = {}

export const CUE_POINTS: readonly CuePoint[] = ['turn_complete', 'approval_needed', 'error']

export function setCueVoices(next: Partial<Record<CuePoint, CueName>> | undefined): void {
  const clean: Partial<Record<CuePoint, CueName>> = {}
  if (next) {
    for (const point of CUE_POINTS) {
      const v = next[point]
      if (v && Object.hasOwn(CUES, v)) clean[point] = v
    }
  }
  voices = clean
}

export function cueVoice(point: CuePoint): CueName {
  return voices[point] ?? point
}

export const SOUND_CUES_KEY = 'soundCues'
const ENABLED_VALUE = 'on'

let ctx: AudioContext | null = null
let armed = false

export function soundCuesEnabled(): boolean {
  try {
    return localStorage.getItem(SOUND_CUES_KEY) === ENABLED_VALUE
  } catch {
    return false
  }
}

function audioCtor(): typeof AudioContext | null {
  if (typeof window === 'undefined') return null
  return (
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext ||
    null
  )
}

function ensureContext(): AudioContext | null {
  if (ctx) return ctx
  const Ctor = audioCtor()
  if (!Ctor) return null
  try {
    ctx = new Ctor()
  } catch {
    return null
  }
  return ctx
}

export function armCueAudio(): void {
  if (armed || ctx) return
  if (!audioCtor()) return
  armed = true
  const onGesture = () => {
    if (!soundCuesEnabled()) return
    ensureContext()
    if (!ctx) return
    window.removeEventListener('pointerdown', onGesture)
    window.removeEventListener('keydown', onGesture)
  }
  window.addEventListener('pointerdown', onGesture)
  window.addEventListener('keydown', onGesture)
}

export function setSoundCuesEnabled(on: boolean): void {
  try {
    if (on) localStorage.setItem(SOUND_CUES_KEY, ENABLED_VALUE)
    else localStorage.removeItem(SOUND_CUES_KEY)
  } catch {
  }
  if (on) {
    const c = ensureContext()
    if (c && c.state === 'suspended') void c.resume().catch(() => {})
  }
}

export function playCue(point: CuePoint, voice?: CueName): void {
  if (!soundCuesEnabled()) return
  if (prefersReducedMotion()) return
  if (typeof document !== 'undefined' && document.hidden) return
  const c = ctx
  if (!c) return
  try {
    if (c.state === 'suspended') void c.resume().catch(() => {})
    synth(c, CUES[voice && Object.hasOwn(CUES, voice) ? voice : cueVoice(point)])
  } catch {
  }
}

function synth(c: AudioContext, recipe: CueRecipe): void {
  const peak = Math.min(Math.max(recipe.gain, 0), MAX_GAIN)
  const slice = recipe.durMs / 1000 / Math.max(recipe.freqs.length, 1)
  const t0 = c.currentTime
  recipe.freqs.forEach((freq, i) => {
    const osc = c.createOscillator()
    const gain = c.createGain()
    osc.type = recipe.wave
    osc.frequency.value = freq
    const start = t0 + i * slice
    const end = start + slice
    gain.gain.setValueAtTime(0, start)
    gain.gain.linearRampToValueAtTime(peak, start + Math.min(0.008, slice * 0.25))
    gain.gain.exponentialRampToValueAtTime(0.0001, end)
    osc.connect(gain)
    gain.connect(c.destination)
    osc.start(start)
    osc.stop(end)
  })
}
