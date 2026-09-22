import { motion } from 'framer-motion'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { Compass, ArrowUpRight, Play, X } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { Button } from '../../shared/ui/Button'
import { IconButton } from '../../shared/ui/IconButton'
import { WorkbenchLayout } from '../../shared/ui/WorkbenchLayout'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { spring } from '../../shared/theme/motion'
import { EntranceGroup, EntranceRegion } from '../../shared/ui/motion'
import { useQuery } from '../../shared/data/data'
import { api, type DiscoverTip, type DiscoverTryIt } from '../../shared/data/api'
import type { RouteProps } from '../../app/shell/useQueryState'
import { PageTitle } from '../../shared/ui/PageTitle'
import { requestProductTour } from '../onboarding/tourLaunch'
import { accentChip } from '../../shared/theme/accent'

export function DiscoverPage({ navigate }: Pick<RouteProps, 'navigate'>) {
  const { data, error, refresh } = useQuery(
    'discover', () => api.discover(), { persist: false },
  )

  const dismiss = async (id: string) => {
    if (!(await reportingWrite('dismiss that tip', () => api.dismissDiscoverTip(id)))) return
    refresh()
  }

  const restore = async () => {
    if (!(await reportingWrite('restore dismissed tips', () => api.clearDismissedDiscoverTips()))) return
    refresh()
  }

  return (
    <WorkbenchLayout
      topBar={
        <TopBar
          keepCornerPadding
          left={
            <PageTitle className="flex items-center gap-s">
              Discover
              {data && data.enabled && data.visible_count > 0 && (
                <span
                  data-type="label-s"
                  className="inline-flex h-5 items-center rounded-pill px-2"
                  style={accentChip}
                >
                  {data.visible_count}
                </span>
              )}
            </PageTitle>
          }
        />
      }
    >
      <div className="mx-auto flex flex-col gap-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
        {
}
        <ReplayTourCard />
        {data === undefined && error ? (
          <LoadError what="tips" error={error} onRetry={refresh} />
        ) : data === undefined ? (
          <ListSkeleton rows={6} what="tips" />
        ) : !data || !data.enabled ? (
          <EmptyState
            icon={Compass}
            title="Discover is off"
            hint="Curated tips that guide you to the parts of Gideon you haven't tried yet. Turn them back on in Settings › Legibility."
            action={{ label: 'Open Settings', onClick: () => navigate('settings/legibility'), icon: Compass }}
          />
        ) : data.visible_count === 0 ? (
          <EmptyState
            icon={Compass}
            title="No Discover tips to show"
            hint={emptyDiscoverReason(data.restorable_count ?? 0, data.engaged_count ?? 0)}
            action={data.restorable_count ? { label: 'Restore dismissed tips', onClick: restore } : undefined}
          />
        ) : (
          <EntranceGroup className="flex flex-col gap-2xl">
            {
}
            <EntranceRegion>
              <p data-type="body-m" className="max-w-[520px] text-on-surface-var">
                The parts of Gideon you haven&rsquo;t tried yet. Your sidebar starts short and
                grows as you open things &mdash; this is where you find out what else is there. Each
                tip links straight into the feature; dismiss any you&rsquo;re not interested in.
              </p>
            </EntranceRegion>
            {data.areas.map((group) => (
              <EntranceRegion key={group.area} className="min-w-0">
                <section className="flex min-w-0 flex-col gap-m">
                  <div className="flex items-center gap-s">
                    {
}
                    <h2 data-type="label-l" className="text-on-surface-var">{group.area}</h2>
                    <span className="h-px flex-1 bg-outline-variant/40" />
                  </div>
                  <div className="flex flex-col gap-s">
                    {group.tips.map((tip, i) => (
                      <TipRow
                        key={tip.id}
                        tip={tip}
                        index={i}
                        onGo={() => navigate(tryItPath(tip.try_it))}
                        onDismiss={() => dismiss(tip.id)}
                      />
                    ))}
                  </div>
                </section>
              </EntranceRegion>
            ))}
          </EntranceGroup>
        )}
      </div>
    </WorkbenchLayout>
  )
}

function ReplayTourCard() {
  return (
    <div className="flex items-center gap-m rounded-lg bg-surface-container px-l py-m">
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg"
        style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
        <Play size={18} className="text-primary" aria-hidden="true" />
      </span>
      <div className="min-w-0 flex-1">
        <p data-type="label-l" className="text-on-surface">Replay the tour</p>
        <p data-type="body-m" className="mt-xs text-on-surface-var">
          The two-minute walk through the sidebar, chat, the Inbox, approvals and Settings.
          Escape ends it at any point.
        </p>
      </div>
      <Button variant="tonal" size="sm" onClick={requestProductTour} className="shrink-0">
        Start the tour
      </Button>
    </div>
  )
}

function TipRow({ tip, index, onGo, onDismiss }: { tip: DiscoverTip; index: number; onGo: () => void; onDismiss: () => void }) {
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0, marginTop: 0, transition: spring.spatialFast }}
      transition={{ ...spring.spatialDefault, delay: Math.min(index * 0.03, 0.3) }}
      className="group flex items-center gap-m rounded-lg bg-surface-container px-l py-m transition-colors hover:bg-surface-high"
    >
      <span className="inline-flex size-10 shrink-0 items-center justify-center rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
        <Compass size={19} className="text-primary" />
      </span>
      <div className="min-w-0 flex-1">
        <p data-type="label-l" className="text-on-surface">{tip.title}</p>
        <p data-type="body-m" className="mt-xs text-on-surface-var">{tip.lesson}</p>
      </div>
      <Button variant="tonal" size="sm" onClick={onGo} className="group/go shrink-0">
        {tip.try_it.label}
        <ArrowUpRight size={14} className="transition-transform group-hover/go:translate-x-px group-hover/go:-translate-y-px" />
      </Button>
      <IconButton
        icon={X}
        label="Dismiss — don't suggest this again"
        onClick={onDismiss}
        size={34}
        className="shrink-0 text-on-surface-low"
      />
    </motion.div>
  )
}

function tryItPath(t: DiscoverTryIt): string {
  const q = new URLSearchParams(t.query ?? {}).toString()
  return q ? `${t.route}?${q}` : t.route
}

export function emptyDiscoverReason(restorable: number, engaged: number): string {
  if (restorable && engaged) return `${engaged} hidden because you tried those features; ${restorable} dismissed by you. Restore them to see them again.`
  if (restorable) return `You dismissed ${restorable} tip${restorable === 1 ? '' : 's'}. Restore them to see them again.`
  if (engaged) return `You already tried the feature${engaged === 1 ? '' : 's'} behind ${engaged === 1 ? 'this tip' : `these ${engaged} tips`}. New tips will appear as Gideon grows.`
  return 'There are no curated tips available right now. New tips will appear as Gideon grows.'
}
