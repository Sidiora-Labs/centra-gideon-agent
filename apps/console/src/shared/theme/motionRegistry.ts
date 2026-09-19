import type { Transition } from 'framer-motion'

type CubicBezier = [number, number, number, number]

interface PhysicsDefinition {
  stiffness: number
  dampingAtPlayful: number
  calmDamping: number
}

export const motionRegistry = {
  ease: {
    emphasized: [0.22, 0.61, 0.13, 1] as CubicBezier,
    emphasizedDecel: [0.08, 0.7, 0.12, 1] as CubicBezier,
    emphasizedAccel: [0.34, 0, 0.75, 0.12] as CubicBezier,
  },
  duration: {
    short: 0.1,
    medium: 0.3,
    long: 0.5,
  },
  spring: {
    spatialDefault: { type: 'spring', stiffness: 380, damping: 30, mass: 1 },
    spatialFast: { type: 'spring', stiffness: 800, damping: 34, mass: 1 },
    spatialSlow: { type: 'spring', stiffness: 200, damping: 26, mass: 1 },
    effects: { duration: 0.2, ease: [0.2, 0, 0, 1] },
  } satisfies Record<string, Transition>,
  physics: {
    snappy: { stiffness: 520, dampingAtPlayful: 30, calmDamping: 40 },
    smooth: { stiffness: 320, dampingAtPlayful: 34, calmDamping: 38 },
    fluid: { stiffness: 180, dampingAtPlayful: 26, calmDamping: 34 },
    playful: { stiffness: 420, dampingAtPlayful: 14, calmDamping: 34 },
  } satisfies Record<string, PhysicsDefinition>,
  variants: {
    messageEnter: {
      initial: { opacity: 0, y: 8 },
      animate: { opacity: 1, y: 0 },
    },
    overlayEnter: {
      initial: { opacity: 0, scale: 0.96, y: 4 },
      animate: { opacity: 1, scale: 1, y: 0 },
      exit: { opacity: 0, scale: 0.98 },
    },
    thinkingPulse: {
      animate: { opacity: [0.45, 0.85, 0.45], scale: [1, 1.04, 1] },
      reduced: { opacity: 0.85, scale: 1 },
      transition: { duration: 3.2, ease: 'easeInOut', repeat: Infinity } satisfies Transition,
    },
    listItemEnter: {
      initial: { opacity: 0, y: 8 },
      animate: { opacity: 1, y: 0 },
    },
  },
}

export const registeredMotionFamilies = [
  ...Object.keys(motionRegistry.spring).map((name) => `spring.${name}`),
  ...Object.keys(motionRegistry.physics).map((name) => `physics.${name}`),
  ...Object.keys(motionRegistry.variants),
] as const
