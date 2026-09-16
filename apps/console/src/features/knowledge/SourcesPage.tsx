import { useState } from 'react'
import { ArrowLeft, Plus, Rss, ShieldOff, Link2, MonitorPlay, AlertTriangle, Zap, type LucideIcon } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { IconButton } from '../../shared/ui/IconButton'
import { PageTitle } from '../../shared/ui/PageTitle'
import { Button } from '../../shared/ui/Button'
import { Toggle } from '../../shared/ui/Toggle'
import { TextInput } from '../../shared/ui/forms'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { api, type WatchedSource } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { notify } from '../../app/shell/appSdk'
import { relFuture, relPast } from '../schedule/scheduleMeta'
import { fvs } from '../../shared/theme/fontWeight'
import { RAW_ENRICHMENT, TONE_CLASS, eventDrivenMetaLine, fmtInterval, formIcon, healthMeta } from './sourceMeta'

type KindIndex = Record<string, { display_name: string; form: string }>

function Chip({ label, tone, icon: Icon, title }: {
  label: string
  tone?: 'ok' | 'warn' | 'danger' | 'neutral'
  icon?: LucideIcon
  title?: string
}) {
  const ink = tone && tone !== 'neutral' ? TONE_CLASS[tone] : 'text-on-surface-var'
  return (
    <span title={title} data-type="caption" className={`inline-flex items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 ${ink}`} style={fvs(500)}>
      {Icon && <Icon size={11} aria-hidden />}
      {label}
    </span>
  )
}

function Remediation({ source, onChanged }: { source: WatchedSource; onChanged: () => void }) {
  const rem = source.remediation
  const [url, setUrl] = useState(String(source.spec?.url ?? ''))
  const [busy, setBusy] = useState(false)
  if (!rem?.kind) return null

  const isRender = rem.kind === 'render_tier'

  async function apply(body: Parameters<typeof api.updateKnowledgeSource>[1], done: string) {
    setBusy(true)
    try {
      await api.updateKnowledgeSource(source.id, body)
      notify(done, 'success')
      onChanged()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'That change did not save', 'error')
    } finally { setBusy(false) }
  }

  return (
    <div className="mt-m rounded-lg bg-surface-high/60 p-m">
      <div className="flex items-start gap-2">
        {isRender ? <MonitorPlay size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />
          : <Link2 size={15} className="mt-0.5 shrink-0 text-warn" aria-hidden />}
        <div className="min-w-0 flex-1">
          {
}
          <p data-type="body-s" className="text-on-surface leading-relaxed">{rem.guidance}</p>
          {rem.detail && (
            <p data-type="caption" className="mt-1 text-on-surface-low">{rem.detail}</p>
          )}

          {rem.action === 'allow_render' && (
            <Button size="xs" variant="tonal" className="mt-m" loading={busy}
              onClick={() => apply({ budget: { ...(source.budget || {}), allow_render: true } }, `${source.name} may now use the render tier`)}>
              Allow the render tier
            </Button>
          )}

          {rem.action === 'edit_url' && (
            <div className="mt-m flex flex-wrap items-center gap-s">
              <span className="min-w-0 flex-1 basis-56">
                <TextInput value={url} onChange={setUrl} size="sm" placeholder="https://…/changelog"
                  ariaLabel={`Listing-page URL for ${source.name}`} />
              </span>
              <Button size="xs" variant="tonal" loading={busy}
                disabled={!/^https?:\/\//.test(url.trim()) || url.trim() === String(source.spec?.url ?? '')}
                disabledReason={!/^https?:\/\//.test(url.trim()) ? 'Enter a URL starting with http:// or https://' : 'That is already this source’s URL'}
                onClick={() => apply({ spec: { ...(source.spec || {}), url: url.trim() } }, `${source.name} now watches ${url.trim()}`)}>
                Point it here
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export function SourceRow({ source, index, kinds, onChanged }: {
  source: WatchedSource
  index?: number
  kinds: KindIndex
  onChanged: () => void
}) {
  const kind = kinds[source.provider]
  const Icon = formIcon(kind?.form ?? '')
  const health = healthMeta(source.health_status)
  const [busy, setBusy] = useState(false)

  async function setEnabled(on: boolean) {
    setBusy(true)
    try {
      await api.updateKnowledgeSource(source.id, { enabled: on })
      notify(`${source.name} ${on ? 'resumed' : 'paused'}`, 'success')
      onChanged()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'That change did not save', 'error')
    } finally { setBusy(false) }
  }

  return (
    <ListRow index={index}>
      <div className="flex min-w-0 items-start gap-m">
        <span className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-high">
          <Icon size={16} className="text-on-surface-var" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span data-type="title-m" className="min-w-0 truncate text-on-surface" style={fvs(500)}>{source.name}</span>
            {
}
            {source.event_driven ? null : source.last_poll_at
              ? <Chip label={health.label} tone={health.tone} title={health.hint} />
              : <Chip label="Not polled yet" title="No poll has run, so there is no health to report yet." />}
            {
}
            {source.enrichment === RAW_ENRICHMENT && (
              <Chip label="no AI" icon={ShieldOff}
                title="Raw source: items are indexed and embedded locally, and never reach a model." />
            )}
            {!source.enabled && !source.event_driven && <Chip label="Paused" title="This source is not being polled." />}
            {
}
            {source.event_driven ? (
              <Chip label="Live" icon={Zap} title="Indexed as artifacts change — this source is not polled." />
            ) : !source.enrolled && (
              <Chip label="No provider" tone="danger" icon={AlertTriangle}
                title={`Nothing is registered to poll a ${source.provider} source, so this row will never collect anything.`} />
            )}
          </div>
          {
}
          {
}
          {source.event_driven ? (
            <p data-type="caption" className="mt-1 text-on-surface-low">{eventDrivenMetaLine()}</p>
          ) : (
          <p data-type="caption" className="mt-1 text-on-surface-low">
            {kind?.display_name ?? source.provider}
            {' · every '}{fmtInterval(source.poll_interval_secs)}
            {' · '}{source.last_poll_at ? `polled ${relPast(source.last_poll_at)}` : 'never polled'}
            {source.last_poll_at ? ` · ${source.last_new_count ?? 0} new last time` : ''}
            {
}
            {source.enabled && source.next_poll_at ? ` · next ${relFuture(source.next_poll_at)}` : ''}
          </p>
          )}
          {!!source.last_escalations?.length && (
            <p data-type="caption" className="mt-1 text-on-surface-low">{source.last_escalations.join(' · ')}</p>
          )}
          <Remediation source={source} onChanged={onChanged} />
        </div>
        {
}
        {!source.event_driven && (
          <Toggle on={source.enabled} size="sm" disabled={busy}
            label={`${source.enabled ? 'Pause' : 'Resume'} ${source.name}`}
            onChange={setEnabled} />
        )}
      </div>
    </ListRow>
  )
}

export function SourcesPage({ onBack, onCreate }: { onBack: () => void; onCreate: () => void }) {
  const { data, loading, error, refresh } = useQuery('knowledge:sources', () => api.knowledgeSources())
  const reload = () => { invalidateKeys('knowledge:sources'); refresh() }

  const kinds: KindIndex = Object.fromEntries(
    (data?.kinds ?? []).map((k) => [k.provider, { display_name: k.display_name, form: k.form }]),
  )
  const sources = data?.sources

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<div className="flex items-center gap-s"><IconButton icon={ArrowLeft} label="Back to knowledge" size={40} onClick={onBack} /><PageTitle>Watched sources</PageTitle></div>}
        right={
          <HeaderActions>
            <HeaderControl icon={Plus} label="Watch something" variant="primary" priority="primary"
              hint="Add a page, feed or folder to watch" onClick={onCreate} />
          </HeaderActions>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {
}
          {sources === undefined && error ? (
            <LoadError what="watched sources" error={error} onRetry={reload} />
          ) : sources === undefined || loading ? (
            <ListSkeleton rows={3} what="watched sources" />
          ) : sources.length === 0 ? (
            <EmptyState icon={Rss} title="Nothing watched yet"
              hint="Point Gideon at a changelog, a feed or a folder and new entries land in your library on their own."
              action={{ label: 'Watch something', onClick: onCreate, icon: Plus }} />
          ) : (
            <div className="flex flex-col gap-m">
              {sources.map((s, i) => (
                <SourceRow key={s.id} source={s} index={i} kinds={kinds} onChanged={reload} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
