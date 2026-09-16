/**
 * PERSONALITY-THEMES §S2 (PT-5) — the gideon-arcade proof, driven end to end.
 *
 * The two proof identities are the plan's deliverable, and what makes them a proof
 * rather than a description is that their dials and their cue voice travel the SHIPPED
 * path: `activate` → the appearance store's token overrides → its runtime bridge →
 * `design/runtime.ts`, which is what the canvas and the motion presets actually read.
 * Nothing here writes `runtime` directly; if the provider wrote it behind the store's
 * back, the store would overwrite it on the next token change and the identity would
 * lose its temperament at the first slider drag.
 *
 * 🔑 EVERY RESTORE ASSERTION PICKS AN IDENTITY WHOSE VALUE DIFFERS FROM THE TOKEN
 * DEFAULT. "Switching back restored the dial" is trivially true for a dial the
 * identity happened to set to its default value — `bounciness` is exactly that case
 * for gideon-arcade (both 1). So each dial's round trip is driven from a personality
 * that genuinely moves it, and the existence of one is asserted rather than assumed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import type { PersonalityDials } from '../../shared/theme/personalities'

vi.mock('../../shared/data/api', () => ({
  api: { themes: () => new Promise(() => {}), theme: () => new Promise(() => {}) },
}))

const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider, usePersonality } = await import('./personality')
const { DEFAULT_PERSONALITY, PERSONALITIES, PERSONALITY_DIAL_TOKENS } =
  await import('../../shared/theme/personalities')
const { TOKENS } = await import('../../shared/theme/tokenRegistry')
const { runtime } = await import('../../shared/theme/runtime')
const { CUE_POINTS, cueVoice } = await import('../../shared/theme/soundCues')

type DialName = keyof PersonalityDials

function dialToken(dial: DialName) {
  const varName = PERSONALITY_DIAL_TOKENS[dial]
  const token = TOKENS.find((t) => t.varName === varName)
  if (!token || token.kind === 'color' || !token.runtimeKey) {
    throw new Error(`dial ${dial} → ${varName} is not a runtime-backed token`)
  }
  return token
}

const DIALS = Object.keys(PERSONALITY_DIAL_TOKENS) as DialName[]
const DEFAULTS = Object.fromEntries(
  DIALS.map((d) => [d, dialToken(d).value]),
) as Record<DialName, number | string>

let activate: (id: string) => void = () => {}

function Probe() {
  activate = usePersonality().activate
  return null
}

function mount() {
  return render(
    <AppearanceProvider>
      <PersonalityProvider>
        <Probe />
      </PersonalityProvider>
    </AppearanceProvider>,
  )
}

const live = (dial: DialName) => runtime[dialToken(dial).runtimeKey as keyof typeof runtime]

const ORIGINAL_MATCH_MEDIA = window.matchMedia
const RUNTIME_SNAPSHOT = { ...runtime }

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  localStorage.clear()
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA,
  })
  Object.assign(runtime, RUNTIME_SNAPSHOT)
  localStorage.clear()
  document.documentElement.removeAttribute('data-personality')
  document.documentElement.removeAttribute('style')
})

describe('the proofs declare dials at all', () => {
  it('at least one identity presets every dial, and none is the default identity', () => {
    for (const dial of DIALS) {
      const declaring = PERSONALITIES.filter((p) => p.behavior.dials?.[dial] !== undefined)
      expect(declaring.map((p) => p.id), `nothing presets ${dial}`).not.toEqual([])
      expect(declaring.map((p) => p.id)).not.toContain(DEFAULT_PERSONALITY)
    }
  })

  it('gideon-arcade is the sparkle/bouncy proof the plan names', () => {
    const arcade = PERSONALITIES.find((p) => p.id === 'gideon-arcade')!
    expect(arcade.behavior.dials?.dotShape).toBe('sparkle')
    expect(arcade.behavior.dials?.expressiveness).toBe(1)
    expect(arcade.behavior.soundCues?.turn_complete).toBe('coin_blip')
  })
})

describe('activating an identity writes its dials through the appearance store', () => {
  it('gideon-arcade lands in runtime AND on the document, in one pass', () => {
    mount()
    act(() => activate('gideon-arcade'))
    expect(runtime.dotShape, 'the canvas reads this every frame').toBe('sparkle')
    expect(runtime.dotPattern).toBe('diamond')
    expect(runtime.expressiveness).toBe(1)
    expect(document.documentElement.style.getPropertyValue('--dot-shape')).toBe('sparkle')
    expect(document.documentElement.style.getPropertyValue('--expressiveness')).toBe('1')
  })

  it('retro-terminal lands the OPPOSITE temperament on the same dials', () => {
    mount()
    act(() => activate('retro-terminal'))
    expect(runtime.dotShape).toBe('square')
    expect(runtime.dotPattern).toBe('grid')
    expect(runtime.bounciness).toBe(0)
    expect(runtime.expressiveness).toBe(0.25)
  })

  it('switching between the two proofs replaces the dials, never merges them', () => {
    mount()
    act(() => activate('gideon-arcade'))
    act(() => activate('retro-terminal'))
    expect(runtime.dotShape, 'the arcade sparkle survived the switch').toBe('square')
    expect(runtime.expressiveness).toBe(0.25)
  })
})

describe('switching back to the default identity restores every dial', () => {
  it.each(DIALS.map((d) => [d] as const))('%s returns to its token default', (dial) => {
    // Driven from an identity that MOVES this dial — see the header. `bounciness`
    // would otherwise be proven by gideon-arcade, which sets it to the default anyway.
    const mover = PERSONALITIES.find(
      (p) => p.behavior.dials?.[dial] !== undefined && p.behavior.dials[dial] !== DEFAULTS[dial],
    )
    expect(mover, `no identity moves ${dial} off its default — this rail is vacuous`).toBeDefined()

    mount()
    act(() => activate(mover!.id))
    expect(live(dial), `${mover!.id} did not move ${dial}`).toBe(mover!.behavior.dials![dial])
    act(() => activate(DEFAULT_PERSONALITY))
    expect(live(dial), `${dial} kept the ${mover!.id} value`).toBe(DEFAULTS[dial])
  })

  it('and clears the override, so the user’s own dial is not pinned afterwards', () => {
    mount()
    act(() => activate('gideon-arcade'))
    const pinned = JSON.parse(localStorage.getItem('appearance') ?? '{}')
    expect(pinned.selects?.['--dot-shape'], 'the arcade wrote no override to clear').toBe('sparkle')
    expect(pinned.scalars?.['--expressiveness']).toBe(1)

    act(() => activate(DEFAULT_PERSONALITY))
    const stored = JSON.parse(localStorage.getItem('appearance') ?? '{}')
    expect(stored.selects?.['--dot-shape'], 'the dot-shape override was pinned').toBeUndefined()
    expect(stored.scalars?.['--expressiveness']).toBeUndefined()
  })
})

describe('the cue voice follows the identity', () => {
  it('gideon-arcade installs the coin blip on a finished turn', () => {
    mount()
    act(() => activate('gideon-arcade'))
    expect(cueVoice('turn_complete')).toBe('coin_blip')
    expect(cueVoice('approval_needed')).toBe('approval_needed')
    expect(cueVoice('error')).toBe('error')
  })

  it('retro-terminal installs the bell, and the arcade coin is gone', () => {
    mount()
    act(() => activate('gideon-arcade'))
    act(() => activate('retro-terminal'))
    expect(cueVoice('approval_needed')).toBe('terminal_bell')
    expect(cueVoice('turn_complete'), 'the previous identity’s voice leaked').toBe('turn_complete')
  })

  it('the default identity leaves no voice behind', () => {
    mount()
    act(() => activate('retro-terminal'))
    act(() => activate(DEFAULT_PERSONALITY))
    for (const point of CUE_POINTS) expect(cueVoice(point), point).toBe(point)
  })

  it('a reload under an identity re-installs its voice', () => {
    localStorage.setItem('personality', 'gideon-arcade')
    mount()
    expect(cueVoice('turn_complete')).toBe('coin_blip')
  })
})
