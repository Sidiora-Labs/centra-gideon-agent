import { AnimatePresence, motion } from 'framer-motion'
import { Compass, ArrowUpRight, ArrowRight, Settings, X } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { SlotEmptyState, SlotAction } from './kit'
import { instant, physics, spring, useReducedMotion } from '../../../shared/theme/motion'
import { Button } from '../../../shared/ui/Button'
import { IconButton } from '../../../shared/ui/IconButton'
import type { DiscoverTip, DiscoverTryIt } from '../../../shared/data/api'
import type { RouteProps } from '../../../app/shell/useQueryState'

const BEHIND = 4

const STEP_Y = 4
const STEP_SCALE = 0.02
const STEP_OPACITY = 0.3

const DECK_PAD = BEHIND * STEP_Y + 2

export function Discover({ navigate }: RouteProps) {
  const { discover, discoverErr, dismissDiscoverTip } = useDashboardLive()
  const reduce = useReducedMotion()

  if (discoverErr && !discover) {
    return <SlotEmptyState icon={Compass}>Couldn&rsquo;t load your tips.</SlotEmptyState>
  }
  if (!discover) return null
  if (!discover.enabled) {
    return (
      <SlotEmptyState
        icon={Compass}
        action={<SlotAction icon={Settings} onClick={() => navigate('settings/legibility')}>Open Settings</SlotAction>}
      >
        Discover tips are off.
      </SlotEmptyState>
    )
  }
  const tips = discover.areas.flatMap((a) => a.tips)
  if (tips.length === 0) {
    return (
      <SlotEmptyState icon={Compass}>
        You&rsquo;ve explored every part of Gideon. Nice.
      </SlotEmptyState>
    )
  }

  const deck = tips.slice(0, BEHIND + 1)

  return (
    <div className="flex flex-col gap-s">
      {
}
      <div className="relative" style={{ paddingTop: DECK_PAD }}>
        <AnimatePresence initial={false} mode="popLayout">
          {deck
            .map((tip, depth) => ({ tip, depth }))
            .reverse()
            .map(({ tip, depth }) => (
              <TipCard
                key={tip.id}
                tip={tip}
                depth={depth}
                reduce={!!reduce}
                onGo={() => navigate(tryItPath(tip.try_it))}
                onDismiss={() => dismissDiscoverTip(tip.id)}
              />
            ))}
        </AnimatePresence>
      </div>
      <Button variant="ghost" size="xs" onClick={() => navigate('discover')} className="group self-start text-on-surface-var">
        {tips.length > 1 ? `See all ${tips.length} in Discover` : 'Open Discover'}
        <ArrowRight size={14} className="transition-transform group-hover:translate-x-px" />
      </Button>
    </div>
  )
}

function TipCard({ tip, depth, reduce, onGo, onDismiss }: {
  tip: DiscoverTip
  depth: number
  reduce: boolean
  onGo: () => void
  onDismiss: () => void
}) {
  const isFront = depth === 0
  const transition = reduce ? instant : physics.fluid

  return (
    <motion.div
      layout
      layoutId={`discover-card-${tip.id}`}
      initial={{ opacity: 0, scale: 1 - STEP_SCALE * (depth + 1), y: -STEP_Y * (depth + 1) }}
      animate={{
        opacity: isFront ? 1 : STEP_OPACITY,
        scale: 1 - STEP_SCALE * depth,
        y: -STEP_Y * depth,
        transition,
      }}
      exit={{ opacity: 0, scale: 0.96, x: 24, transition: reduce ? instant : spring.spatialFast }}
      aria-hidden={!isFront}
      style={isFront ? undefined : { top: DECK_PAD, height: '100%' }}
      className={[
        'rounded-lg bg-surface-low',
        isFront ? 'relative flex flex-col gap-s px-m py-m' : 'pointer-events-none absolute inset-x-0',
        isFront ? 'ring-1 ring-outline-variant/40' : 'ring-1 ring-outline-variant/25',
      ].join(' ')}
    >
      {
}
      {!isFront ? null : (
      <>
      <div className="flex items-start gap-s">
        <Compass size={15} className="mt-0.5 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <p data-type="label-l" className="truncate text-on-surface">{tip.title}</p>
          <p data-type="body-m" className="mt-xs text-on-surface-var">{tip.lesson}</p>
        </div>
        <IconButton
          icon={X}
          label="Dismiss — don't suggest this again"
          onClick={onDismiss}
          size={28}
          iconSize={14}
          className="-mr-xs shrink-0 text-on-surface-low"
        />
      </div>
      <Button variant="tonal" size="xs" onClick={onGo} className="group self-start">
        {tip.try_it.label}
        <ArrowUpRight size={13} className="transition-transform group-hover:translate-x-px group-hover:-translate-y-px" />
      </Button>
      </>
      )}
    </motion.div>
  )
}

function tryItPath(t: DiscoverTryIt): string {
  const q = new URLSearchParams(t.query ?? {}).toString()
  return q ? `${t.route}?${q}` : t.route
}
