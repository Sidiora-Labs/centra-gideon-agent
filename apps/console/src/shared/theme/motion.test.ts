import { describe, it, expect, afterEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import * as motion from './motion'
import {
  dragElastic, dragSpring, instant, listItemEnter, overlayEnter, physics,
  prefersReducedMotion, regionStagger, spring, swipeDismiss, viewTransition,
} from './motion'
import { runtime } from './runtime'
import { TOKENS } from './tokenRegistry'


const ORIGINAL_MATCH_MEDIA = window.matchMedia

function defineMatchMedia(value: typeof window.matchMedia): void {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value })
}

function setReducedMotion(on: boolean): void {
  defineMatchMedia(((query: string) => ({
    matches: on && query.includes('prefers-reduced-motion'),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia)
}

const DEFAULTS = { bounciness: runtime.bounciness, dragElastic: runtime.dragElastic, swipeVelocity: runtime.swipeVelocity, swipeDistance: runtime.swipeDistance, expressiveness: runtime.expressiveness }

afterEach(() => {
  vi.restoreAllMocks()
  defineMatchMedia(ORIGINAL_MATCH_MEDIA)
  Object.assign(runtime, DEFAULTS)
})

function resolveVariant(v: unknown): Record<string, unknown> {
  expect(typeof v).toBe('function')
  return (v as (custom: unknown) => Record<string, unknown>)(undefined)
}

const PRESETS = ['snappy', 'smooth', 'fluid', 'playful'] as const

const ENDPOINTS: Record<(typeof PRESETS)[number], [number, number]> = {
  snappy: [40, 30],
  smooth: [38, 34],
  fluid: [34, 26],
  playful: [34, 14],
}

describe('physics presets', () => {
  it('is a CLOSED set of four — no parallel spring vocabulary alongside it', () => {
    expect(Object.keys(physics).sort()).toEqual([...PRESETS].sort())
    expect(motion).not.toHaveProperty('springs')
    expect(motion).not.toHaveProperty('bounce')
    expect(motion).not.toHaveProperty('pressable')
  })

  it.each(PRESETS)('%s scales its overshoot with the bounciness slider', (name) => {
    const [calm, playful] = ENDPOINTS[name]

    runtime.bounciness = 0
    const atCalm = physics[name] as { type?: string; damping?: number; stiffness?: number }
    runtime.bounciness = 1
    const atPlayful = physics[name] as { type?: string; damping?: number; stiffness?: number }

    expect(atCalm.type).toBe('spring')
    expect(atCalm.damping).toBe(calm)
    expect(atPlayful.damping).toBe(playful)
    expect(atPlayful.damping!).toBeLessThan(atCalm.damping!)
    expect(atPlayful.stiffness).toBe(atCalm.stiffness)
  })

  it('reads the slider at ANIMATION time, not once at import', () => {
    runtime.bounciness = 1
    const before = (physics.playful as { damping?: number }).damping
    runtime.bounciness = 0.5
    expect((physics.playful as { damping?: number }).damping).not.toBe(before)
  })

  it.each(PRESETS)('%s zeroes out under prefers-reduced-motion', (name) => {
    setReducedMotion(true)
    const t = physics[name] as { type?: string; duration?: number; damping?: number; stiffness?: number }
    expect(t).toEqual(instant)
    expect(t.duration).toBe(0)
    expect(t.type).not.toBe('spring')
    expect(t.stiffness).toBeUndefined()
    expect(t.damping).toBeUndefined()
  })

  it('survives a spread that overrides one field (the reduced-motion leak)', () => {
    setReducedMotion(true)
    const spread = { ...physics.fluid, stiffness: 240 }
    expect(spread.type).toBe('tween')
    expect(spread.duration).toBe(0)
  })

  it('prefersReducedMotion answers the media query, and false without one', () => {
    setReducedMotion(true)
    expect(prefersReducedMotion()).toBe(true)
    setReducedMotion(false)
    expect(prefersReducedMotion()).toBe(false)
  })
})

const SPATIAL = ['spatialDefault', 'spatialFast', 'spatialSlow'] as const
const SPRING_KEYS = [...SPATIAL, 'effects'] as const

describe('spring tiers — the fixed-damping family', () => {
  it('is a closed set of four, and every member is a GETTER', () => {
    expect(Object.keys(spring).sort()).toEqual([...SPRING_KEYS].sort())
    for (const key of SPRING_KEYS) {
      const d = Object.getOwnPropertyDescriptor(spring, key)
      expect(typeof d?.get, `spring.${key} must be a getter, not a value`).toBe('function')
    }
  })

  it.each(SPATIAL)('%s collapses to instant under prefers-reduced-motion', (key) => {
    setReducedMotion(true)
    const t = spring[key] as { type?: string; duration?: number; stiffness?: number; damping?: number }
    expect(t).toEqual(instant)
    expect(t.type).not.toBe('spring')
    expect(t.stiffness).toBeUndefined()
    expect(t.damping).toBeUndefined()
  })

  it('effects collapses too — a 0.2s crossfade is 0.2s of motion', () => {
    expect((spring.effects as { duration?: number }).duration).toBe(0.2)
    setReducedMotion(true)
    expect(spring.effects).toEqual(instant)
  })

  it.each(SPRING_KEYS)('%s reads the gate at ANIMATION time, not once at import', (key) => {
    const moving = spring[key]
    setReducedMotion(true)
    expect(spring[key]).toEqual(instant)
    setReducedMotion(false)
    expect(spring[key]).toEqual(moving)
  })

  it('is bounciness-INVARIANT, deliberately — the taste dial belongs to physics', () => {
    runtime.bounciness = 0
    const calm = SPRING_KEYS.map((k) => spring[k])
    runtime.bounciness = 1
    const playful = SPRING_KEYS.map((k) => spring[k])
    expect(playful).toEqual(calm)
    for (const t of calm.slice(0, SPATIAL.length)) {
      expect((t as { type?: string }).type).toBe('spring')
    }
  })

  it('survives the delay-spread that 18 real list call sites use', () => {
    setReducedMotion(true)
    const spread = { ...spring.spatialDefault, delay: 0.09 }
    expect(spread.type).toBe('tween')
    expect(spread.duration).toBe(0)
    expect(spread.delay).toBe(0.09)
  })
})

describe('preset-bearing variants', () => {
  it.each([['overlayEnter', overlayEnter], ['listItemEnter', listItemEnter]] as const)(
    '%s resolves its preset fresh on every animation', (_name, variants) => {
      runtime.bounciness = 1
      const playful = resolveVariant(variants.animate).transition as { damping?: number }
      runtime.bounciness = 0
      const calm = resolveVariant(variants.animate).transition as { damping?: number }
      expect(playful.damping).not.toBe(calm.damping)

      setReducedMotion(true)
      expect(resolveVariant(variants.animate).transition).toEqual(instant)
    },
  )

  it('overlayEnter.exit is a FUNCTION too — the one shape that re-snapshots the gate', () => {
    setReducedMotion(true)
    expect(resolveVariant(overlayEnter.exit).transition).toEqual(instant)
    setReducedMotion(false)
    expect(resolveVariant(overlayEnter.exit).transition).not.toEqual(instant)
  })
})


const TRANSITION_KEYS = new Set([
  'type', 'duration', 'ease', 'delay', 'stiffness', 'damping', 'mass', 'bounce',
  'velocity', 'restDelta', 'restSpeed', 'repeat', 'repeatType', 'repeatDelay',
  'staggerChildren', 'delayChildren', 'staggerDirection', 'times', 'when', 'from',
  'visualDuration',
])

const SPRING_FIELDS = ['stiffness', 'damping', 'mass', 'bounce', 'restDelta', 'restSpeed'] as const

function looksLikeTransition(v: unknown): v is Record<string, unknown> {
  if (v === null || typeof v !== 'object' || Array.isArray(v)) return false
  const keys = Object.keys(v)
  return keys.length > 0 && keys.every((k) => TRANSITION_KEYS.has(k))
}

function harvestTransitions(): { path: string; t: Record<string, unknown> }[] {
  const found: { path: string; t: Record<string, unknown> }[] = []

  const consider = (path: string, v: unknown): void => {
    if (v === null || v === undefined) return
    if (typeof v === 'function') {
      if ((v as { length: number }).length !== 0) return
      consider(`${path}()`, (v as () => unknown)())
      return
    }
    if (typeof v !== 'object') return
    if (looksLikeTransition(v)) { found.push({ path, t: v }); return }
    for (const [k, child] of Object.entries(v as Record<string, unknown>)) consider(`${path}.${k}`, child)
  }

  for (const [name, value] of Object.entries(motion)) consider(name, value)
  return found
}

const NOT_WALKED = [
  'duration', 'dragElastic', 'ease', 'expr', 'exprHeavy', 'prefersReducedMotion',
  'swipeDismiss', 'viewTransition',
]

describe('the reduced-motion off-switch, enumerated over the module', () => {
  it('finds every family — the vacuity guard on the walk itself', () => {
    const harvest = harvestTransitions()
    const families = new Set(harvest.map((h) => h.path.split(/[.(]/)[0]))

    expect(harvest.length, 'the walker found (almost) nothing — check looksLikeTransition')
      .toBeGreaterThanOrEqual(14)
    expect(families.size, `only reached: ${[...families].join(', ')}`).toBeGreaterThanOrEqual(8)
    for (const key of SPRING_KEYS) expect(harvest.map((h) => h.path)).toContain(`spring.${key}`)

    const walked = new Set([...families])
    const missed = Object.keys(motion).filter((k) => !walked.has(k)).sort()
    expect(missed).toEqual([...NOT_WALKED].sort())
  })

  it('the walk can SEE a spring — so a clean result means something', () => {
    setReducedMotion(false)
    const springs = harvestTransitions().filter(
      (h) => h.t.type === 'spring' || SPRING_FIELDS.some((f) => h.t[f] !== undefined),
    )
    expect(springs.length, 'the walk found no springs with motion ALLOWED').toBeGreaterThanOrEqual(8)
    const paths = springs.map((s) => s.path)
    expect(paths.some((p) => p.startsWith('spring.'))).toBe(true)
    expect(paths.some((p) => p.startsWith('physics.'))).toBe(true)
  })

  it('yields ZERO springs under prefers-reduced-motion — every family, no exceptions', () => {
    setReducedMotion(true)
    const offenders = harvestTransitions()
      .filter((h) => h.t.type === 'spring' || SPRING_FIELDS.some((f) => h.t[f] !== undefined))
      .map((h) => `${h.path} → ${JSON.stringify(h.t)}`)
    expect(offenders, `springs survived the off-switch:\n  ${offenders.join('\n  ')}`).toEqual([])
  })

  it('and every transition it yields is instant or a pure tween', () => {
    setReducedMotion(true)
    for (const { path, t } of harvestTransitions()) {
      expect(t.type === undefined || t.type === 'tween', `${path} type=${String(t.type)}`).toBe(true)
      for (const f of SPRING_FIELDS) expect(t[f], `${path}.${f}`).toBeUndefined()
    }
  })
})

describe('regionStagger — the surface entrance choreography', () => {
  it('is a stagger, and delays nothing before the first region', () => {
    const t = regionStagger() as { staggerChildren?: number; delayChildren?: number }
    expect(t.delayChildren).toBe(0)
    expect(t.staggerChildren).toBeGreaterThan(0)
  })

  it('scales its step with expressiveness — and stays TIGHT, not dead, at 0', () => {
    runtime.expressiveness = 1
    const bold = (regionStagger() as { staggerChildren: number }).staggerChildren
    runtime.expressiveness = 0
    const refined = (regionStagger() as { staggerChildren: number }).staggerChildren
    expect(refined).toBeLessThan(bold)
    expect(refined).toBeGreaterThan(0)
    runtime.expressiveness = 0.5
    const mid = (regionStagger() as { staggerChildren: number }).staggerChildren
    expect(mid).toBeCloseTo((bold + refined) / 2, 10)
  })

  it('lands on stagger()`s own default step at the app default expressiveness', () => {
    runtime.expressiveness = DEFAULTS.expressiveness
    const t = regionStagger() as { staggerChildren: number }
    expect(t.staggerChildren).toBeCloseTo(0.044, 3)
  })

  it('returns NULL under prefers-reduced-motion — an absence, not a faster cascade', () => {
    setReducedMotion(true)
    expect(regionStagger()).toBeNull()
  })

  it('reads reduced motion at CALL time, not once at import', () => {
    expect(regionStagger()).not.toBeNull()
    setReducedMotion(true)
    expect(regionStagger()).toBeNull()
    setReducedMotion(false)
    expect(regionStagger()).not.toBeNull()
  })

  it('takes no arguments, so no surface can pick its own cascade', () => {
    expect(regionStagger.length).toBe(0)
  })
})

describe('gesture helpers', () => {
  it('dragSpring is a bounciness-scaled spring that zeroes under reduced motion', () => {
    runtime.bounciness = 0
    const calm = dragSpring() as { type?: string; damping?: number }
    runtime.bounciness = 1
    const playful = dragSpring() as { type?: string; damping?: number }
    expect(calm.type).toBe('spring')
    expect(playful.damping!).toBeLessThan(calm.damping!)

    setReducedMotion(true)
    expect(dragSpring()).toEqual(instant)
  })

  it('dragElastic reads its token and clamps to 0..1', () => {
    runtime.dragElastic = 0.4
    expect(dragElastic()).toBe(0.4)
    runtime.dragElastic = 5
    expect(dragElastic()).toBe(1)
    runtime.dragElastic = -1
    expect(dragElastic()).toBe(0)
  })

  it('dragElastic stays put under reduced motion — the drag is a FUNCTION, not decoration', () => {
    setReducedMotion(true)
    runtime.dragElastic = 0.9
    expect(dragElastic()).toBe(0.9)
  })

  it('swipeDismiss dismisses on a fast flick OR a slow haul past the distance', () => {
    expect(swipeDismiss(runtime.swipeVelocity + 1, 0).dismiss).toBe(true)
    expect(swipeDismiss(0, runtime.swipeDistance + 1).dismiss).toBe(true)
    expect(swipeDismiss(runtime.swipeVelocity - 1, runtime.swipeDistance - 1).dismiss).toBe(false)
    expect(swipeDismiss(-(runtime.swipeVelocity + 1), 0).dismiss).toBe(true)
  })

  it('swipeDismiss thresholds follow their tokens', () => {
    runtime.swipeVelocity = 1200
    runtime.swipeDistance = 200
    expect(swipeDismiss(600, 100).dismiss).toBe(false)
    expect(swipeDismiss(1300, 0).dismiss).toBe(true)
    expect(swipeDismiss(0, 240).dismiss).toBe(true)
  })

  it('resolves a kept swipe with the return spring and a dismissed one with an exit curve', () => {
    const kept = swipeDismiss(0, 0)
    expect(kept.dismiss).toBe(false)
    expect((kept.transition as { type?: string }).type).toBe('spring')

    const gone = swipeDismiss(9999, 0)
    expect(gone.dismiss).toBe(true)
    expect((gone.transition as { type?: string }).type).not.toBe('spring')
    expect((gone.transition as { duration?: number }).duration).toBeGreaterThan(0)
  })

  it('both swipe branches go instant under reduced motion, verdict unchanged', () => {
    setReducedMotion(true)
    expect(swipeDismiss(9999, 0)).toEqual({ dismiss: true, transition: instant })
    expect(swipeDismiss(0, 0)).toEqual({ dismiss: false, transition: instant })
  })
})

describe('viewTransition', () => {
  type Svt = (cb: () => void) => unknown
  const NEVER = new Promise<void>(() => {})
  const hangingTransition = { ready: NEVER, finished: NEVER, updateCallbackDone: NEVER, skipTransition: () => {} }

  function install(impl: Svt | undefined): void {
    if (impl) Object.defineProperty(document, 'startViewTransition', { configurable: true, writable: true, value: impl })
    else Reflect.deleteProperty(document, 'startViewTransition')
  }

  afterEach(() => { install(undefined) })

  it('runs the update directly when the platform has no View Transitions API', () => {
    expect(document.startViewTransition).toBeUndefined()
    let ran = 0
    viewTransition(() => { ran += 1 })
    expect(ran).toBe(1)
  })

  it('runs the update through the transition when the platform supports it', () => {
    const seen: string[] = []
    install((cb) => { seen.push('started'); cb(); return hangingTransition })
    viewTransition(() => { seen.push('updated') })
    expect(seen).toEqual(['started', 'updated'])
  })

  it('runs the update even when startViewTransition THROWS', () => {
    install(() => { throw new Error('no transition for you') })
    let ran = 0
    viewTransition(() => { ran += 1 })
    expect(ran).toBe(1)
  })

  it('runs the update EXACTLY once when the API invokes the callback and then throws', () => {
    install((cb) => { cb(); throw new Error('threw after invoking') })
    let ran = 0
    viewTransition(() => { ran += 1 })
    expect(ran).toBe(1)
  })

  it('re-raises an error thrown by the UPDATE instead of recovering from it', () => {
    install((cb) => { cb(); return hangingTransition })
    let ran = 0
    expect(() => viewTransition(() => { ran += 1; throw new Error('render blew up') }))
      .toThrow('render blew up')
    expect(ran).toBe(1)
  })

  it('never awaits the animation — a transition that never settles still applies the update', () => {
    install((cb) => { cb(); return hangingTransition })
    let ran = false
    viewTransition(() => { ran = true })
    expect(ran).toBe(true)
  })

  it('does not start a transition under reduced motion, and still applies the update', () => {
    setReducedMotion(true)
    const started = vi.fn((cb: () => void) => { cb(); return hangingTransition })
    install(started)
    let ran = 0
    viewTransition(() => { ran += 1 })
    expect(started).not.toHaveBeenCalled()
    expect(ran).toBe(1)
  })

  it('reads reduced motion at CALL time, not once at import', () => {
    const started = vi.fn((cb: () => void) => { cb(); return hangingTransition })
    install(started)
    viewTransition(() => {})
    expect(started).toHaveBeenCalledTimes(1)
    setReducedMotion(true)
    viewTransition(() => {})
    expect(started).toHaveBeenCalledTimes(1)
  })
})

describe('motion token round trip', () => {
  const css = readFileSync(join(process.cwd(), "src/shared/theme/tokens.css"), 'utf8')
  const runtimeScalars = TOKENS.filter((t) => t.kind === 'scalar' && t.runtimeKey)

  it('the gesture-physics tokens are registered in the Motion group', () => {
    for (const varName of ['--drag-elastic', '--swipe-dismiss-velocity', '--swipe-dismiss-distance']) {
      const t = TOKENS.find((x) => x.varName === varName)
      expect(t, `${varName} missing from tokenRegistry`).toBeTruthy()
      expect(t!.group).toBe('Motion')
    }
  })

  it.each(runtimeScalars.map((t) => [t.varName, t] as const))(
    '%s round-trips registry → tokens.css → runtime', (varName, token) => {
      expect(css, `${varName} has no default in tokens.css`).toContain(`${varName}:`)
      const key = (token as { runtimeKey?: string }).runtimeKey!
      expect(runtime, `runtime.${key} missing for ${varName}`).toHaveProperty(key)
      expect(typeof (runtime as unknown as Record<string, unknown>)[key]).toBe('number')
    },
  )

  it('every registered scalar default matches its tokens.css declaration', () => {
    for (const t of runtimeScalars) {
      const m = new RegExp(`${t.varName}:\\s*([^;]+);`).exec(css)
      expect(m, `${t.varName} not declared in tokens.css`).toBeTruthy()
      const declared = parseFloat(m![1])
      expect(declared, `${t.varName}: registry ${(t as { value: number }).value} vs css ${m![1]}`)
        .toBe((t as { value: number }).value)
    }
  })
})
