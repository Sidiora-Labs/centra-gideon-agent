import { Select } from '../../shared/ui/forms'
import { useMemo, useState } from 'react'
import { DatabaseZap, RefreshCw } from 'lucide-react'
import { api, type HypermidCachePlanWire, type HypermidCacheWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { Section } from '../settings/settingsUI'
import { inspectionQueryKey } from './hypermidState'

const FRESHNESS = ['', 'fresh', 'stale', 'rebuilding', 'unavailable', 'unreadable'] as const

function bytes(value: number): string {
  if (value < 1024) return `${value} B`
  if (value < 1024 ** 2) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 ** 2).toFixed(1)} MB`
}

function CachePlan({ plan, onClose }: { plan: HypermidCachePlanWire; onClose: () => void }) {
  return <section aria-labelledby="hypermid-cache-plan-title" className="mt-m rounded-lg border border-outline-variant bg-surface p-l">
    <div className="flex items-start justify-between gap-m">
      <div><h3 id="hypermid-cache-plan-title" className="text-base text-on-surface">Review {plan.action} plan</h3>
        <p className="mt-xs text-sm text-on-surface-low">{plan.recovery}</p></div>
      <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
    </div>
    <dl className="mt-m grid gap-s text-sm sm:grid-cols-2">
      <div><dt className="text-on-surface-low">Affected entries</dt><dd className="text-on-surface">{plan.entries.length}</dd></div>
      <div><dt className="text-on-surface-low">Affected data</dt><dd className="text-on-surface">{bytes(plan.entries.reduce((total, entry) => total + entry.bytes, 0))}</dd></div>
    </dl>
    <p data-type="caption" className="mt-m text-on-surface-low">This is a read-only plan. Apply it from Maintenance after reviewing the final scope and plan digest.</p>
  </section>
}

function CacheRow({ cache, onPlan }: { cache: HypermidCacheWire; onPlan: (plan: HypermidCachePlanWire) => void }) {
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const plan = async (action: 'clear' | 'rebuild') => {
    setBusy(action); setError('')
    try { onPlan(await api.planHypermidCache(cache.id, action)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The plan could not be prepared.') }
    finally { setBusy('') }
  }
  const tone = cache.freshness === 'fresh' ? 'ok' : cache.freshness === 'stale' || cache.freshness === 'rebuilding' ? 'warn' : 'muted'
  return <Surface tone="container" radius="lg" className="p-l">
    <div className="flex flex-wrap items-start justify-between gap-m">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-s"><DatabaseZap size={16} className="text-primary" aria-hidden />
          <h3 className="text-sm text-on-surface">{cache.kind.replaceAll('_', ' ')}</h3><StatusPill label={cache.freshness} tone={tone} /></div>
        <p data-type="caption" className="mt-xs text-on-surface-low">Generation {cache.generation} · {bytes(cache.bytes)} · {cache.hits ?? 'Unknown'} hits · {cache.misses ?? 'Unknown'} misses</p>
        <p data-type="caption" className="mt-xs text-on-surface-low">{cache.observed_at ? `Observed ${new Date(cache.observed_at).toLocaleString()}` : 'Observation time unknown'}</p>
      </div>
      <div className="flex flex-wrap gap-s">
        <Button size="sm" variant="secondary" loading={busy === 'rebuild'} onClick={() => void plan('rebuild')}>Plan rebuild</Button>
        <Button size="sm" variant="secondary" loading={busy === 'clear'} onClick={() => void plan('clear')}>Plan clear</Button>
      </div>
    </div>
    {error && <p role="alert" className="mt-s text-sm text-danger">{error}</p>}
  </Surface>
}

export function CacheInspector() {
  const [freshness, setFreshness] = useState<(typeof FRESHNESS)[number]>('')
  const [plan, setPlan] = useState<HypermidCachePlanWire>()
  const filters = useMemo(() => ({ freshness: freshness || undefined }), [freshness])
  const caches = useQuery(inspectionQueryKey('caches', filters), () => api.hypermidCaches(filters))
  return <Section title="Caches and projections" hint="Inspect freshness and recovery impact before clearing or rebuilding scoped derived state."
    right={<Button size="sm" variant="secondary" onClick={caches.refresh}><RefreshCw size={14} /> Refresh</Button>}>
    <div className="mb-m flex justify-end">
      <div className="w-full sm:w-56"><Select value={freshness} onChange={(value) => setFreshness(value as typeof freshness)} ariaLabel="Cache freshness" className="min-h-11 w-full rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary sm:w-56" options={[...FRESHNESS.map(value => ({ value: value, label: value ? value.charAt(0).toUpperCase() + value.slice(1) : 'All cache states' }))]} /></div>
    </div>
    {caches.error && !caches.data ? <LoadError what="Hypermid caches" error={caches.error} onRetry={caches.refresh} />
      : !caches.data ? <ListSkeleton what="Hypermid caches" />
      : caches.data.state === 'unavailable' || caches.data.state === 'unreadable'
        ? <LoadError what="Hypermid caches" error={caches.data.detail || caches.data.state} onRetry={caches.refresh} />
        : caches.data.items.length === 0 ? <EmptyState title="No matching caches" hint={freshness ? 'Choose another cache state.' : 'No derived cache entries are available in this scope.'} />
        : <div className="grid gap-m">{caches.data.items.map((cache) => <CacheRow key={cache.id} cache={cache} onPlan={setPlan} />)}</div>}
    {plan && <CachePlan plan={plan} onClose={() => setPlan(undefined)} />}
    {caches.data?.state === 'filtered' && <p data-type="caption" className="mt-s text-on-surface-low">Showing filtered cache entries. This view does not replace the complete inventory.</p>}
  </Section>
}
