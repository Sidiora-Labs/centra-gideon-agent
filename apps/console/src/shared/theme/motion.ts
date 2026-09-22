
import { useEffect, useState } from 'react'
import type { Transition, Variants } from 'framer-motion'

import { motionRegistry } from './motionRegistry'
import { runtime } from './runtime'

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(prefersReducedMotion)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
    const media = window.matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(media.matches)
    update()
    media.addEventListener?.('change', update)
    return () => media.removeEventListener?.('change', update)
  }, [])

  return reduced
}

export const instant: Transition = { type: 'tween', duration: 0 }

function gated(t: Transition): Transition {
  return prefersReducedMotion() ? instant : t
}

function family<T extends Record<string, unknown>>(
  definitions: T,
  resolve: (definition: T[keyof T]) => Transition,
): { [K in keyof T]: Transition } {
  const result = {} as { [K in keyof T]: Transition }
  for (const name of Object.keys(definitions) as (keyof T)[]) {
    Object.defineProperty(result, name, {
      enumerable: true,
      get: () => resolve(definitions[name]),
    })
  }
  return result
}

export const spring = family(motionRegistry.spring, (definition) => gated({ ...definition }))

function bouncy(stiffness: number, dampingAtPlayful: number, calmDamping: number): Transition {
  const b = Math.max(0, Math.min(1, runtime.bounciness))
  const damping = calmDamping + (dampingAtPlayful - calmDamping) * b
  return gated({ type: 'spring', stiffness, damping, mass: 1 })
}

export const physics = family(motionRegistry.physics, (definition) => bouncy(
  definition.stiffness,
  definition.dampingAtPlayful,
  definition.calmDamping,
))

export const ease = motionRegistry.ease

export const duration = motionRegistry.duration

export const messageEnter: Variants = {
  initial: motionRegistry.variants.messageEnter.initial,
  animate: {
    ...motionRegistry.variants.messageEnter.animate,
    transition: { duration: duration.medium, ease: ease.emphasizedDecel },
  },
}

export const overlayEnter: Variants = {
  initial: motionRegistry.variants.overlayEnter.initial,
  animate: () => ({ ...motionRegistry.variants.overlayEnter.animate, transition: physics.playful }),
  exit: () => ({ ...motionRegistry.variants.overlayEnter.exit, transition: spring.effects }),
}

export const thinkingPulse: Variants = {
  animate: () => prefersReducedMotion()
    ? { ...motionRegistry.variants.thinkingPulse.reduced, transition: instant }
    : {
        ...motionRegistry.variants.thinkingPulse.animate,
        transition: { ...motionRegistry.variants.thinkingPulse.transition },
      },
}

export function stagger(step = 0.04, delayChildren = 0): Transition {
  return { staggerChildren: step, delayChildren }
}

export const listItemEnter: Variants = {
  initial: motionRegistry.variants.listItemEnter.initial,
  animate: () => ({ ...motionRegistry.variants.listItemEnter.animate, transition: physics.smooth }),
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
