
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'


interface Tone { wave: string; freq: number; start: number }

let constructed = 0
let tones: Tone[] = []
let ramps: { value: number; at: number }[] = []
let resumeCalls = 0
let instances: FakeAudioContext[] = []

class FakeAudioContext {
  state: 'running' | 'suspended' = 'running'
  currentTime = 10
  destination = { id: 'destination' }

  constructor() {
    constructed++
    instances.push(this)
  }

  resume(): Promise<void> {
    resumeCalls++
    this.state = 'running'
    return Promise.resolve()
  }

  createOscillator() {
    const osc = {
      type: 'sine' as string,
      frequency: { value: 0 },
      connect: () => {},
      start: (t: number) => tones.push({ wave: osc.type, freq: osc.frequency.value, start: t }),
      stop: () => {},
    }
    return osc
  }

  createGain() {
    return {
      gain: {
        setValueAtTime: () => {},
        linearRampToValueAtTime: (value: number, at: number) => ramps.push({ value, at }),
        exponentialRampToValueAtTime: () => {},
      },
      connect: () => {},
    }
  }
}

const ORIGINAL_MATCH_MEDIA = window.matchMedia

function setReducedMotion(on: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: ((query: string) => ({
      matches: on && query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    })) as unknown as typeof window.matchMedia,
  })
}

function setTabHidden(hidden: boolean): void {
  Object.defineProperty(document, 'hidden', { configurable: true, value: hidden })
}

async function load() {
  vi.resetModules()
  return import('./soundCues')
}

beforeEach(() => {
  constructed = 0
  tones = []
  ramps = []
  resumeCalls = 0
  instances = []
  localStorage.clear()
  setReducedMotion(false)
  setTabHidden(false)
  Object.defineProperty(window, 'AudioContext', {
    configurable: true,
    writable: true,
    value: FakeAudioContext as unknown as typeof AudioContext,
  })
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA,
  })
  setTabHidden(false)
  localStorage.clear()
})

async function audible() {
  const m = await load()
  localStorage.setItem(m.SOUND_CUES_KEY, 'on')
  m.armCueAudio()
  window.dispatchEvent(new Event('pointerdown'))
  expect(constructed, 'the baseline must have a real context, or every test below passes vacuously').toBe(1)
  return m
}

describe('the master toggle defaults OFF', () => {
  it('an absent preference is off', async () => {
    const m = await load()
    expect(localStorage.getItem(m.SOUND_CUES_KEY)).toBeNull()
    expect(m.soundCuesEnabled()).toBe(false)
  })

  it("only the literal 'on' enables sound — every other value is off", async () => {
    const m = await load()
    for (const bad of ['', 'off', '1', 'true', 'yes', 'ON', 'on ', '{}']) {
      localStorage.setItem(m.SOUND_CUES_KEY, bad)
      expect(m.soundCuesEnabled(), `'${bad}' must not enable sound`).toBe(false)
    }
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    expect(m.soundCuesEnabled()).toBe(true)
  })

  it('turning it on persists it and turning it off removes the key', async () => {
    const m = await load()
    m.setSoundCuesEnabled(true)
    expect(localStorage.getItem(m.SOUND_CUES_KEY)).toBe('on')
    m.setSoundCuesEnabled(false)
    expect(localStorage.getItem(m.SOUND_CUES_KEY)).toBeNull()
  })
})

describe('a cue plays when nothing suppresses it', () => {
  it('schedules the recipe: one tone per frequency, in order, on the declared wave', async () => {
    const m = await audible()
    m.playCue('turn_complete')
    const recipe = m.CUES.turn_complete
    expect(tones.map((t) => t.freq)).toEqual(recipe.freqs)
    expect(tones.every((t) => t.wave === recipe.wave)).toBe(true)
    expect(tones[0].start).toBeGreaterThanOrEqual(10)
    expect(tones[1].start).toBeGreaterThan(tones[0].start)
  })

  it('plays each of the three closed cues', async () => {
    const m = await audible()
    for (const name of ['turn_complete', 'approval_needed', 'error'] as const) {
      tones = []
      m.playCue(name)
      expect(tones.length, name).toBe(m.CUES[name].freqs.length)
    }
  })

  it('resumes a context the browser suspended while the tab was in the background', async () => {
    const m = await audible()
    resumeCalls = 0
    instances[0].state = 'suspended'
    m.playCue('error')
    expect(resumeCalls, 'a suspended context must be resumed, not scheduled onto').toBe(1)
    expect(tones.length).toBeGreaterThan(0)
  })
})


describe('the master toggle alone silences a cue', () => {
  it('is silent with cues off while reduced-motion is CLEAR and the tab is VISIBLE', async () => {
    const m = await audible()
    localStorage.removeItem(m.SOUND_CUES_KEY)
    expect(window.matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(false)
    expect(document.hidden).toBe(false)
    m.playCue('turn_complete')
    expect(tones).toEqual([])
  })
})

describe('prefers-reduced-motion alone silences a cue', () => {
  it('is silent under reduced motion while cues are ON and the tab is VISIBLE', async () => {
    const m = await audible()
    setReducedMotion(true)
    expect(m.soundCuesEnabled(), 'the toggle must stay ON or this proves nothing').toBe(true)
    expect(document.hidden).toBe(false)
    m.playCue('turn_complete')
    expect(tones).toEqual([])
  })

  it('and goes audible again the moment the query clears — read live, never cached', async () => {
    const m = await audible()
    setReducedMotion(true)
    m.playCue('error')
    expect(tones).toEqual([])
    setReducedMotion(false)
    m.playCue('error')
    expect(tones.length).toBeGreaterThan(0)
  })
})

describe('a hidden tab alone silences a cue', () => {
  it('is silent while hidden with cues ON and reduced-motion CLEAR', async () => {
    const m = await audible()
    setTabHidden(true)
    expect(m.soundCuesEnabled(), 'the toggle must stay ON or this proves nothing').toBe(true)
    expect(window.matchMedia('(prefers-reduced-motion: reduce)').matches).toBe(false)
    m.playCue('turn_complete')
    expect(tones).toEqual([])
  })
})


describe('exactly one AudioContext, and only from a user gesture', () => {
  it('importing the module constructs nothing', async () => {
    await load()
    expect(constructed).toBe(0)
  })

  it('playCue NEVER constructs a context — no gesture, no sound, no leak', async () => {
    const m = await load()
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    m.playCue('turn_complete')
    expect(constructed, 'a context built outside a gesture is a dead context').toBe(0)
    expect(tones).toEqual([])
  })

  it('the armed primer builds the context on the first gesture, then stops listening', async () => {
    const m = await load()
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    m.armCueAudio()
    expect(constructed, 'arming alone must not construct — the gesture does').toBe(0)
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed).toBe(1)
    window.dispatchEvent(new Event('pointerdown'))
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'a' }))
    m.armCueAudio()
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed, 'a second context is a leaked audio thread').toBe(1)
  })

  it('a keypress counts as the gesture too — keyboard-only users get cues', async () => {
    const m = await load()
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    m.armCueAudio()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }))
    expect(constructed).toBe(1)
  })

  it('keeps listening through gestures made while cues are off, then primes once enabled', async () => {
    const m = await load()
    m.armCueAudio()
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed, 'nothing to prime while the toggle is off').toBe(0)
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed, 'the primer must not have given up on the first miss').toBe(1)
  })

  it('turning the toggle ON primes inside that click, so the next cue is audible', async () => {
    const m = await load()
    m.setSoundCuesEnabled(true)
    expect(constructed, 'the toggle click IS the user activation — use it').toBe(1)
    m.playCue('error')
    expect(tones.length).toBeGreaterThan(0)
  })

  it('resumes a context that starts suspended even inside the toggle gesture', async () => {
    class StartsSuspended extends FakeAudioContext {
      constructor() {
        super()
        this.state = 'suspended'
      }
    }
    Object.defineProperty(window, 'AudioContext', {
      configurable: true, writable: true, value: StartsSuspended as unknown as typeof AudioContext,
    })
    const m = await load()
    m.setSoundCuesEnabled(true)
    expect(resumeCalls, 'a suspended context must be resumed at the gesture').toBe(1)
  })

  it('survives a browser with no Web Audio at all, and does not arm a listener for nothing', async () => {
    Object.defineProperty(window, 'AudioContext', { configurable: true, writable: true, value: undefined })
    Object.defineProperty(window, 'webkitAudioContext', { configurable: true, writable: true, value: undefined })
    const m = await load()
    localStorage.setItem(m.SOUND_CUES_KEY, 'on')
    expect(() => {
      m.setSoundCuesEnabled(true)
      m.armCueAudio()
      window.dispatchEvent(new Event('pointerdown'))
      m.playCue('error')
    }).not.toThrow()
    expect(tones).toEqual([])
    Object.defineProperty(window, 'AudioContext', {
      configurable: true, writable: true, value: FakeAudioContext as unknown as typeof AudioContext,
    })
    window.dispatchEvent(new Event('pointerdown'))
    expect(constructed, 'nothing should have been armed, so this gesture builds nothing').toBe(0)
  })

  it('a cue never throws into the surface that fired it', async () => {
    const m = await audible()
    const broken = FakeAudioContext.prototype.createOscillator
    FakeAudioContext.prototype.createOscillator = () => {
      throw new Error('no output device')
    }
    try {
      expect(() => m.playCue('error')).not.toThrow()
    } finally {
      FakeAudioContext.prototype.createOscillator = broken
    }
  })
})


describe('the cue set is closed', () => {
  it('has exactly the three cue POINTS the plan names', async () => {
    const m = await load()
    expect([...m.CUE_POINTS].sort()).toEqual(['approval_needed', 'error', 'turn_complete'])
  })

  it('registers a recipe for every point, plus the two personality voices', async () => {
    const m = await load()
    for (const point of m.CUE_POINTS) expect(m.CUES[point], point).toBeDefined()
    expect(Object.keys(m.CUES).sort()).toEqual([
      'approval_needed', 'coin_blip', 'error', 'terminal_bell', 'turn_complete',
    ])
  })

  it('every recipe is playable: a real wave, at least one positive frequency, real duration', async () => {
    const m = await load()
    const WAVES = new Set(['sine', 'square', 'sawtooth', 'triangle'])
    for (const [name, r] of Object.entries(m.CUES)) {
      expect(WAVES.has(r.wave), `${name}.wave=${r.wave}`).toBe(true)
      expect(r.freqs.length, name).toBeGreaterThan(0)
      expect(r.freqs.every((f) => f > 0 && f < 20000), name).toBe(true)
      expect(r.durMs, name).toBeGreaterThan(0)
      expect(r.durMs, `${name} — a cue is a blip, not a jingle`).toBeLessThanOrEqual(400)
    }
  })

  it('no shipped recipe exceeds the loudness ceiling', async () => {
    const m = await load()
    for (const [name, r] of Object.entries(m.CUES)) {
      expect(r.gain, name).toBeGreaterThan(0)
      expect(r.gain, `${name} must stay under MAX_GAIN`).toBeLessThanOrEqual(m.MAX_GAIN)
    }
  })

  it('clamps a recipe that asks to be louder than the ceiling', async () => {
    const m = await audible()
    m.CUES.error.gain = 5
    m.playCue('error')
    expect(ramps.length).toBeGreaterThan(0)
    expect(Math.max(...ramps.map((r) => r.value))).toBe(m.MAX_GAIN)
  })

  it('clamps a negative gain to silence rather than inverting the wave', async () => {
    const m = await audible()
    m.CUES.error.gain = -0.5
    m.playCue('error')
    expect(ramps.length).toBeGreaterThan(0)
    expect(Math.max(...ramps.map((r) => r.value))).toBe(0)
  })
})


describe('a personality can re-voice a cue point', () => {
  it('plays the installed voice instead of the point’s own', async () => {
    const m = await audible()
    m.setCueVoices({ turn_complete: 'coin_blip' })
    m.playCue('turn_complete')
    expect(tones.map((t) => t.freq)).toEqual(m.CUES.coin_blip.freqs)
    expect(tones.map((t) => t.freq)).not.toEqual(m.CUES.turn_complete.freqs)
  })

  it('leaves the points it does not name on their own voices', async () => {
    const m = await audible()
    m.setCueVoices({ turn_complete: 'coin_blip' })
    m.playCue('error')
    expect(tones.map((t) => t.freq)).toEqual(m.CUES.error.freqs)
  })

  it('clearing the voices restores every point — no residue you can only hear', async () => {
    const m = await audible()
    m.setCueVoices({ turn_complete: 'coin_blip' })
    m.setCueVoices(undefined)
    m.playCue('turn_complete')
    expect(tones.map((t) => t.freq)).toEqual(m.CUES.turn_complete.freqs)
  })

  it('drops an unregistered voice at install time rather than at play time', async () => {
    const m = await audible()
    m.setCueVoices({ turn_complete: 'ka-ching' } as never)
    expect(m.cueVoice('turn_complete')).toBe('turn_complete')
    m.playCue('turn_complete')
    expect(tones.map((t) => t.freq)).toEqual(m.CUES.turn_complete.freqs)
  })

  it('refuses an INHERITED voice name — the prototype-chain hole, closed', async () => {
    const m = await audible()
    for (const inherited of ['constructor', 'toString', 'hasOwnProperty', '__proto__', 'valueOf']) {
      m.setCueVoices({ error: inherited } as never)
      expect(m.cueVoice('error'), inherited).toBe('error')
      tones = []
      expect(() => m.playCue('error')).not.toThrow()
      expect(tones.map((t) => t.freq), inherited).toEqual(m.CUES.error.freqs)
    }
  })

  it('ignores a key that is not a cue point — a voice cannot invent a moment', async () => {
    const m = await audible()
    m.setCueVoices({ turn_complete: 'coin_blip', app_started: 'terminal_bell' } as never)
    expect(m.cueVoice('turn_complete')).toBe('coin_blip')
    expect([...m.CUE_POINTS]).toEqual(['turn_complete', 'approval_needed', 'error'])
  })

  it('a re-voiced cue is still silenced by the master toggle', async () => {
    const m = await audible()
    m.setCueVoices({ turn_complete: 'coin_blip' })
    localStorage.removeItem(m.SOUND_CUES_KEY)
    m.playCue('turn_complete')
    expect(tones).toEqual([])
  })
})


describe('playCue accepts an explicit voice', () => {
  it('plays the named voice instead of the point’s own', async () => {
    const m = await audible()
    m.playCue('turn_complete', 'coin_blip')
    expect(tones.map((t) => t.freq)).toEqual(m.CUES.coin_blip.freqs)
    expect(tones.map((t) => t.freq)).not.toEqual(m.CUES.turn_complete.freqs)
  })

  it('falls back to the point’s voice for an unknown or inherited voice name', async () => {
    const m = await audible()
    for (const bad of ['ka-ching', 'constructor', '__proto__'] as const) {
      tones = []
      m.playCue('error', bad as never)
      expect(tones.map((t) => t.freq), bad).toEqual(m.CUES.error.freqs)
    }
  })

  it('is still silenced by every suppressor', async () => {
    const m = await audible()
    localStorage.removeItem(m.SOUND_CUES_KEY)
    m.playCue('turn_complete', 'coin_blip')
    expect(tones).toEqual([])
  })
})
