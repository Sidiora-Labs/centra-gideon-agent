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

export const motionComponents = [
  'app/shell/App.tsx',
  'features/ChatPage.tsx',
  'features/chat/useStreamCoalescer.ts',
  'features/code/DiffReveal.tsx',
  'features/code/TypingReveal.tsx',
  'features/dashboard/widgets/Discover.tsx',
  'features/dashboard/world/AgentWorld.tsx',
  'features/knowledge/ReadingView.tsx',
  'features/settings/PersonalityPicker.tsx',
  'features/tasks/DagView.tsx',
  'features/tasks/TaskBoard.tsx',
  'features/tasks/TasksListPage.tsx',
  'shared/theme/soundCues.ts',
  'shared/ui/AddItemButton.tsx',
  'shared/ui/Button.tsx',
  'shared/ui/Composer.tsx',
  'shared/ui/ComposerStage.tsx',
  'shared/ui/DotGlow.tsx',
  'shared/ui/FilterMenu.tsx',
  'shared/ui/FilterRow.tsx',
  'shared/ui/GideonMark.tsx',
  'shared/ui/IconButton.tsx',
  'shared/ui/InlineError.tsx',
  'shared/ui/ListScaffold.tsx',
  'shared/ui/Meter.tsx',
  'shared/ui/Modal.tsx',
  'shared/ui/Popover.tsx',
  'shared/ui/ProgressRing.tsx',
  'shared/ui/QuietButton.tsx',
  'shared/ui/SearchField.tsx',
  'shared/ui/Segmented.tsx',
  'shared/ui/SidePanel.tsx',
  'shared/ui/SnipOverlay.tsx',
  'shared/ui/SpotlightTour.tsx',
  'shared/ui/SquareIconButton.tsx',
  'shared/ui/StaleNotice.tsx',
  'shared/ui/TileButton.tsx',
  'shared/ui/Toggle.tsx',
  'shared/ui/TokenControls.tsx',
  'shared/ui/WavyProgress.tsx',
  'shared/ui/chat/StreamingIndicator.tsx',
  'shared/ui/composer/controls.tsx',
  'shared/ui/controlContent.tsx',
  'shared/ui/dialog/DialogShell.tsx',
  'shared/ui/motion/Bud.tsx',
  'shared/ui/motion/ContextMenu.tsx',
  'shared/ui/motion/Disintegrate.tsx',
  'shared/ui/motion/Expandable.tsx',
  'shared/ui/motion/LiquidShape.tsx',
  'shared/ui/motion/Morph.tsx',
  'shared/ui/motion/Reorderable.tsx',
  'shared/ui/personality/TerminalStrip.tsx',
  'shared/ui/widget/BlueprintSkeleton.tsx',
] as const
