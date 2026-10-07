# Motion vocabulary

Motion behavior lives in [motion.ts](../../apps/console/src/shared/theme/motion.ts), with named definitions in [motionRegistry.ts](../../apps/console/src/shared/theme/motionRegistry.ts). Use these shared definitions rather than copying timing values into a feature.

## Choosing a transition

| Family | Purpose |
| --- | --- |
| `physics.snappy` | Small control feedback |
| `physics.smooth` | General spatial transitions |
| `physics.fluid` | Larger surface movement |
| `physics.playful` | Deliberate expressive moments |
| `spring.effects` | Opacity and other effects |

The physics getters resolve current bounciness at access time. `expr()` scales expressive magnitudes, and `exprHeavy()` gates heavier effects. Read dynamic presets when an animation runs; storing a getter result in a module-level object can freeze the settings observed at import.

## Reduced motion

`prefersReducedMotion()` reads the operating-system preference; `useReducedMotion()` also subscribes to changes. Shared gated transitions return the instant transition under reduced motion. `regionStagger()` returns null so entrance regions can render without a cascade.

Components still need to preserve direct manipulation, state information and usability. A global CSS rule or Framer configuration does not prove that every custom animation is suppressed. Test the actual component's reduced-motion branch, especially projection, morphing and continuously animated effects.

## Gestures and navigation

Use `dragSpring()`, `dragElastic()` and `swipeDismiss()` for their supported gesture contracts. Elasticity permits direct manipulation; the settling transition is a separate concern. Preserve thresholds from the configured theme rather than introducing unrelated literals.

`viewTransition(update)` commits the update independently of transition support. Navigation and other user actions must not wait for a decorative animation to finish. A missing browser API or failed transition must not lose the state change.

Entrance and morph primitives are exported from [the motion controls](../../apps/console/src/shared/ui/motion/index.ts). Keep a motion host mounted across the state change it represents; remounting can erase the transition. Text and controls must carry the meaning independently of decorative shapes.

See [patterns](PATTERNS.md) and [browser checking](../../apps/console/e2e/README.md). These source contracts are not a claim of visual or accessibility qualification across all browsers.
