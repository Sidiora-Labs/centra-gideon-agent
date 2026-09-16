import type { Transition } from 'framer-motion'
import { duration, ease, expr, physics, spring } from '../../theme/motion'

export const MORPH_FAMILY = { flight: 190, state: 200, spawn: 240, stiffnessBonus: 70, floor: .4, refinedScale: .7 }
export function familySpring(base: number): Transition {
  const resolved = physics.fluid
  switch (resolved.type) {
    case 'spring': return Object.assign({}, resolved, { stiffness: base + expr(MORPH_FAMILY.stiffnessBonus, MORPH_FAMILY.floor) })
    default: return resolved
  }
}
export function familyTween(heavy: boolean): Transition {
  const scale = heavy ? 1 : MORPH_FAMILY.refinedScale
  return { ease: ease.emphasized, duration: scale * duration.medium }
}
export function familyFade(): Transition { return spring.effects }
