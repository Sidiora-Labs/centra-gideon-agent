
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { MORPH_FAMILY, familyFade, familySpring, familyTween } from './index'
import { duration, ease, exprHeavy, physics, spring } from '../../theme/motion'
import { runtime } from '../../theme/runtime'

const DEFAULT_EXPRESSIVENESS = runtime.expressiveness
afterEach(() => { runtime.expressiveness = DEFAULT_EXPRESSIVENESS })

const MEMBERS = ['Morph.tsx', 'LiquidShape.tsx', 'Bud.tsx', 'Disintegrate.tsx'] as const

const source = (name: string) => readFileSync(join(process.cwd(), "src/shared/ui/motion", name), 'utf8')

const code = (text: string) => text
  .split('\n')
  .filter((l) => {
    const t = l.trim()
    return t !== '' && !t.startsWith('//') && !t.startsWith('*') && !t.startsWith('/*')
  })
  .join('\n')

function motionImports(text: string): string[] | null {
  const m = text.match(/import\s*\{([^}]*)\}\s*from\s*'\.\.\/\.\.\/theme\/motion'/)
  if (!m) return null
  return m[1].split(',').map((s) => s.trim()).filter(Boolean)
}

describe('the four primitives own no timing of their own', () => {
  it('finds all four members, and they are real files', () => {
    expect(MEMBERS).toHaveLength(4)
    for (const name of MEMBERS) {
      const text = source(name)
      expect(code(text), name).toContain(`export function ${name.replace('.tsx', '')}(`)
      expect(code(text), name).toContain('export function')
    }
  })

  it('each imports the family vocabulary', () => {
    for (const name of MEMBERS) {
      expect(code(source(name)), name).toMatch(/from '\.\/vocabulary'/)
    }
  })

  it('none reaches past it for a preset, a curve or a length', () => {
    const AMPLITUDE_ONLY = ['expr', 'exprHeavy']
    for (const name of MEMBERS) {
      const named = motionImports(source(name))
      if (named === null) continue
      expect(named.sort(), `${name} imports timing from design/motion`)
        .toEqual(named.filter((n) => AMPLITUDE_ONLY.includes(n)).sort())
    }
    expect(motionImports(source('Disintegrate.tsx'))).toEqual(['expr', 'exprHeavy'])
  })

  it('none writes a stiffness, a raw duration or a raw bezier', () => {
    for (const name of MEMBERS) {
      const text = code(source(name))
      expect(text, `${name} sets its own stiffness`).not.toMatch(/stiffness:/)
      expect(text, `${name} hardcodes a duration`).not.toMatch(/duration:\s*[\d.]/)
      expect(text, `${name} hardcodes a bezier`).not.toMatch(/ease:\s*\[/)
    }
    const liquid = code(source('LiquidShape.tsx'))
    expect(liquid).toContain("ease: 'linear'")
    expect(liquid).toContain('duration: TUNING.breatheCycle')
  })

  it('the heavy/refined tier splits at ONE threshold for the whole family', () => {
    for (const name of MEMBERS) {
      expect(code(source(name)), `${name} passes a custom heavy threshold`)
        .not.toMatch(/exprHeavy\(\s*[^)\s]/)
    }
    expect(code(source('LiquidShape.tsx'))).toContain('exprHeavy()')
    expect(code(source('Disintegrate.tsx'))).toContain('exprHeavy()')
  })

  it('every member self-gates reduced motion in JS', () => {
    for (const name of MEMBERS) {
      expect(code(source(name)), `${name} does not self-gate`).toContain('useReducedMotion()')
      expect(code(source(name)), `${name} bypasses the shared accessor`).toMatch(/from '\.\.\/\.\.\/theme\/motion'/)
    }
  })
})

describe('all three springs resolve to one preset and one bonus', () => {
  const BASES = [
    ['flight', MORPH_FAMILY.flight],
    ['state', MORPH_FAMILY.state],
    ['spawn', MORPH_FAMILY.spawn],
  ] as const

  const stiffness = (base: number) => (familySpring(base) as { stiffness: number }).stiffness

  it.each(BASES)('%s rides physics.fluid — same damping, same mass', (_name, base) => {
    const t = familySpring(base) as { type?: string; damping?: number; mass?: number }
    const fluid = physics.fluid as { damping?: number; mass?: number }
    expect(t.type).toBe('spring')
    expect(t.damping).toBe(fluid.damping)
    expect(t.mass).toBe(fluid.mass)
  })

  it('adds the SAME bonus to every base — one knob, one meaning', () => {
    const bonuses = BASES.map(([, base]) => stiffness(base) - base)
    for (const b of bonuses) {
      expect(b, `bonuses diverged: ${bonuses.join(', ')}`).toBeCloseTo(bonuses[0], 10)
    }
    expect(bonuses[0]).toBeGreaterThan(0)
  })

  it('BOLD means tauter for every member, in the same direction', () => {
    for (const [name, base] of BASES) {
      runtime.expressiveness = 1
      const bold = stiffness(base)
      runtime.expressiveness = 0
      const refined = stiffness(base)
      expect(bold, `${name} inverts the knob`).toBeGreaterThan(refined)
      expect(bold).toBe(base + MORPH_FAMILY.stiffnessBonus)
    }
  })

  it('orders its bases by TRAVEL — the further it flies, the softer it starts', () => {
    expect(MORPH_FAMILY.flight).toBeLessThan(MORPH_FAMILY.state)
    expect(MORPH_FAMILY.state).toBeLessThan(MORPH_FAMILY.spawn)
  })
})

describe('the family has exactly one tween and one fade', () => {
  it('the dissolve rides the house emphasized curve, both tiers', () => {
    const bold = familyTween(true) as { duration: number; ease: unknown }
    const refined = familyTween(false) as { duration: number; ease: unknown }
    expect(bold.ease).toBe(ease.emphasized)
    expect(refined.ease).toBe(ease.emphasized)
    expect(bold.duration).toBe(duration.medium)
    expect(refined.duration).toBeCloseTo(duration.medium * MORPH_FAMILY.refinedScale, 10)
    expect(refined.duration).toBeLessThan(bold.duration)
    expect(refined.duration).toBeGreaterThan(0)
  })

  it('the fade IS the app fade — no length invented for "something is fading"', () => {
    expect(familyFade()).toBe(spring.effects)
  })
})

describe('expressiveness 0 is the FLOOR, not the off switch', () => {
  it('still returns a real spring, at the floor of the bonus', () => {
    runtime.expressiveness = 0
    for (const base of [MORPH_FAMILY.flight, MORPH_FAMILY.state, MORPH_FAMILY.spawn]) {
      const t = familySpring(base) as { type?: string; stiffness: number }
      expect(t.type).toBe('spring')
      expect(t.stiffness).toBe(base + MORPH_FAMILY.stiffnessBonus * MORPH_FAMILY.floor)
      expect(t.stiffness).toBeGreaterThan(base)
    }
  })

  it('drops the heavy tier — the one thing that does switch OFF at 0', () => {
    runtime.expressiveness = 0
    expect(exprHeavy()).toBe(false)
    runtime.expressiveness = DEFAULT_EXPRESSIVENESS
    expect(exprHeavy()).toBe(true)
    expect((familyTween(false) as { duration: number }).duration)
      .toBeLessThan((familyTween(true) as { duration: number }).duration)
  })
})
