
import type { Transition, Variants } from 'framer-motion'

import { runtime } from './runtime'

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export const instant: Transition = { type: 'tween', duration: 0 }

function gated(t: Transition): Transition {
  return prefersReducedMotion() ? instant : t
}

const SPATIAL_DEFAULT: Transition = { type: 'spring', stiffness: 380, damping: 30, mass: 1 }
const SPATIAL_FAST: Transition = { type: 'spring', stiffness: 800, damping: 34, mass: 1 }
const SPATIAL_SLOW: Transition = { type: 'spring', stiffness: 200, damping: 26, mass: 1 }
const EFFECTS: Transition = { duration: 0.2, ease: [0.2, 0, 0, 1] }

export const spring = {
  get spatialDefault(): Transition { return gated(SPATIAL_DEFAULT) },
  get spatialFast(): Transition { return gated(SPATIAL_FAST) },
  get spatialSlow(): Transition { return gated(SPATIAL_SLOW) },
  get effects(): Transition { return gated(EFFECTS) },
}

function bouncy(stiffness: number, dampingAtPlayful: number, calmDamping: number): Transition {
  const b = Math.max(0, Math.min(1, runtime.bounciness))
  const damping = calmDamping + (dampingAtPlayful - calmDamping) * b
  return gated({ type: 'spring', stiffness, damping, mass: 1 })
}

export const physics = {
  get snappy(): Transition { return bouncy(520, 30, 40) },
  get smooth(): Transition { return bouncy(320, 34, 38) },
  get fluid(): Transition { return bouncy(180, 26, 34) },
  get playful(): Transition { return bouncy(420, 14, 34) },
}

export const ease = {
  emphasized: [0.22, 0.61, 0.13, 1] as [number, number, number, number],
  emphasizedDecel: [0.08, 0.7, 0.12, 1] as [number, number, number, number],
  emphasizedAccel: [0.34, 0, 0.75, 0.12] as [number, number, number, number],
}

export const duration = { short: 0.1, medium: 0.3, long: 0.5 }

export const messageEnter: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0, transition: { duration: duration.medium, ease: ease.emphasizedDecel } },
}

export const overlayEnter: Variants = {
  initial: { opacity: 0, scale: 0.96, y: 4 },
  animate: () => ({ opacity: 1, scale: 1, y: 0, transition: physics.playful }),
  exit: () => ({ opacity: 0, scale: 0.98, transition: spring.effects }),
}

export const thinkingPulse: Variants = {
  animate: {
    opacity: [0.45, 0.85, 0.45],
    scale: [1, 1.04, 1],
    transition: { duration: 3.2, ease: 'easeInOut', repeat: Infinity },
  },
}

export function stagger(step = 0.04, delayChildren = 0): Transition {
  return { staggerChildren: step, delayChildren }
}

export const listItemEnter: Variants = {
  initial: { opacity: 0, y: 8 },
  animate: () => ({ opacity: 1, y: 0, transition: physics.smooth }),
}

const REGION_STEP = 0.05
const REGION_STEP_FLOOR = 0.4

export function regionStagger(): Transition | null {
  if (prefersReducedMotion()) return null
  return stagger(expr(REGION_STEP, REGION_STEP_FLOOR))
}


export function dragSpring(): Transition {
  return bouncy(620, 24, 44)
}

export function dragElastic(): number {
  return Math.max(0, Math.min(1, runtime.dragElastic))
}

export function swipeDismiss(velocity: number, offset = 0): { dismiss: boolean; transition: Transition } {
  const dismiss = Math.abs(velocity) >= runtime.swipeVelocity || Math.abs(offset) >= runtime.swipeDistance
  if (prefersReducedMotion()) return { dismiss, transition: instant }
  return {
    dismiss,
    transition: dismiss
      ? { duration: duration.medium, ease: ease.emphasizedAccel }
      : dragSpring(),
  }
}


export function expr(max: number, floor = 0.35): number {
  const e = Math.max(0, Math.min(1, runtime.expressiveness))
  return max * (floor + (1 - floor) * e)
}

export function exprHeavy(threshold = 0.5): boolean {
  return runtime.expressiveness >= threshold
}

export function viewTransition(update: () => void): void {
  const start = document.startViewTransition as Document['startViewTransition'] | undefined
  if (prefersReducedMotion() || typeof start !== 'function') { update(); return }
  let ran = false
  let updateFailed = false
  const once = () => {
    if (ran) return
    ran = true
    try { update() } catch (err) { updateFailed = true; throw err }
  }
  try {
    start.call(document, once)
  } catch (err) {
    if (updateFailed) throw err
    once()
  }
}
