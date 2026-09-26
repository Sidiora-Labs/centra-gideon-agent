import { useState } from 'react'
import { ArrowLeft, Plus, FileClock, Play, Trash2 } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { HeaderActions, HeaderControl } from '../../shared/ui/HeaderActions'
import { IconButton } from '../../shared/ui/IconButton'
import { PageTitle } from '../../shared/ui/PageTitle'
import { Button } from '../../shared/ui/Button'
import { Toggle } from '../../shared/ui/Toggle'
import { ChipInput, Field, FieldError, TextInput } from '../../shared/ui/forms'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { api, type ResearchReport, type ResearchReportInput } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { notify } from '../../app/shell/appSdk'
import { relPast } from '../schedule/scheduleMeta'
import { BUSY_REASON } from '../../shared/ui/unavailable'
import { ResearchReport as ResearchReportCard } from '../../shared/vendor/assistant-ui/elements/research-report'

const CACHE_KEY = 'knowledge:reports'

function blank(): ResearchReportInput {
  return {
    name: '',
    prompt: '',
    schedule: { kind: 'cron', cron_expr: '0 8 * * *' },
    tz: '',
    source: { tags: [], window_secs: 0 },
    context: null,
    citation_policy: 'cite-source-only',
    iteration_cap: 3,
    enabled: true,
  }
}

function reportDetails(r: ResearchReport): string[] {
  const when = r.schedule.cron_expr
    ? `cron ${r.schedule.cron_expr}`
    : r.schedule.every_secs
      ? `every ${Math.round(r.schedule.every_secs / 60)} min`
      : r.schedule.at_ts
        ? 'once'
        : 'no schedule'
  const watches = r.source.tags.length ? `tagged ${r.source.tags.join(', ')}` : 'anything new'
  const citation = r.citation_policy === 'cite-source-only' ? 'cites new material only' : 'may cite context'
  return [when, watches, citation]
}

export function ReportRow({ report, index, onChanged }: {
  report: ResearchReport
  index?: number
  onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true)
    try {
      await fn()
      notify(ok, 'success')
      onChanged()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'That did not go through', 'error')
    } finally { setBusy(false) }
  }

  return (
    <ListRow index={index}>
      <div className="flex min-w-0 items-start gap-m">
        <span className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg bg-surface-high">
          <FileClock size={16} className="text-on-surface-var" aria-hidden />
        </span>
        <ResearchReportCard compact title={report.name} prompt={report.prompt}
          details={reportDetails(report)}
          lastStatus={report.last_status === 'error' ? 'last run failed' : report.last_status ? `status ${report.last_status}` : undefined}
          statusError={report.last_status === 'error'}
          lastRun={report.last_run_ts ? `ran ${relPast(report.last_run_ts)}` : 'never run'}
          lastError={report.last_error || undefined} />
        <div className="flex shrink-0 items-center gap-s">
          <Toggle on={report.enabled} disabled={busy}
            label={`${report.name} enabled`}
            onChange={(on) => void act(() => api.updateResearchReport(report.id, { enabled: on }),
              `${report.name} ${on ? 'resumed' : 'paused'}`)} />
          <Button size="xs" variant="secondary" disabled={busy} disabledReason={BUSY_REASON}
            ariaLabel={`Run ${report.name} now`}
            onClick={() => void act(() => api.runResearchReport(report.id), `${report.name} started`)}>
            <Play size={13} /> Run now
          </Button>
          {
}
          <IconButton icon={Trash2} label={`Delete ${report.name}`} size={36} loading={busy} tone="danger"
            onClick={() => void act(() => api.deleteResearchReport(report.id), `${report.name} deleted`)} />
        </div>
      </div>
    </ListRow>
  )
}

function CreateForm({ onCreated, onCancel }: { onCreated: () => void; onCancel: () => void }) {
  const [draft, setDraft] = useState<ResearchReportInput>(blank())
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const set = (patch: Partial<ResearchReportInput>) => setDraft((d) => ({ ...d, ...patch }))

  async function save() {
    setSaving(true); setErr('')
    try {
      await api.createResearchReport(draft)
      notify(`${draft.name || 'Report'} created`, 'success')
      onCreated()
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'That did not save')
    } finally { setSaving(false) }
  }

  return (
    <div className="flex flex-col gap-m rounded-xl border border-outline-variant/60 bg-surface-container/50 p-l">
      <Field label="Name">
        <TextInput value={draft.name} onChange={(v) => set({ name: v })} placeholder="Weekly contradiction scan" />
      </Field>
      <Field label="Research prompt" hint="What should it look for in the new material?">
        <TextInput value={draft.prompt} onChange={(v) => set({ prompt: v })}
          placeholder="Find claims that contradict what we already believe, and name both sides." />
      </Field>
      <Field label="Schedule (cron)" hint="Evaluated in the timezone below; a malformed expression is refused rather than stored.">
        <TextInput value={draft.schedule.cron_expr ?? ''}
          onChange={(v) => set({ schedule: { kind: 'cron', cron_expr: v } })} placeholder="0 8 * * *" />
      </Field>
      <Field label="Timezone" hint="Blank uses this machine's timezone.">
        <TextInput value={draft.tz} onChange={(v) => set({ tz: v })} placeholder="America/Los_Angeles" />
      </Field>
      <Field label="New material: tags" hint="Which items count as new material. Empty means anything new.">
        <ChipInput values={draft.source.tags} onChange={(tags) => set({ source: { ...draft.source, tags } })}
          placeholder="Add a tag…" />
      </Field>
      <Field label="Searchable context: tags" hint="What may be searched while writing. Leave empty to search nothing beyond the new material.">
        <ChipInput values={draft.context?.tags ?? []}
          onChange={(tags) => set({ context: tags.length ? { tags, window_secs: 0 } : null })}
          placeholder="Add a tag…" />
      </Field>
      <Field label="Citations" hint="Whether the writing may cite context as well as new material.">
        <div className="flex flex-wrap gap-s">
          {(['cite-source-only', 'allow-citing-context'] as const).map((p) => (
            <Button key={p} size="xs" variant={draft.citation_policy === p ? 'primary' : 'secondary'}
              onClick={() => set({ citation_policy: p })}>
              {p === 'cite-source-only' ? 'New material only' : 'Also allow context'}
            </Button>
          ))}
        </div>
      </Field>
      <Field label="Iteration cap" hint="How many model passes one run may take.">
        <TextInput value={String(draft.iteration_cap)}
          onChange={(v) => set({ iteration_cap: Math.max(1, Number.parseInt(v || '1', 10) || 1) })} />
      </Field>
      {err && <FieldError>{err}</FieldError>}
      <div className="flex items-center gap-s">
        <Button size="sm" disabled={saving || !draft.name.trim() || !draft.prompt.trim()}
          disabledReason={!draft.name.trim() ? 'Name it first' : !draft.prompt.trim() ? 'Give it a prompt' : BUSY_REASON}
          onClick={() => void save()}>Create report</Button>
        <Button size="sm" variant="ghost" disabled={saving} disabledReason={BUSY_REASON} onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}

export function ReportsPage({ onBack }: { onBack: () => void }) {
  const { data, loading, error, refresh } = useQuery(CACHE_KEY, () => api.researchReports())
  const [creating, setCreating] = useState(false)
  const reload = () => { invalidateKeys(CACHE_KEY); refresh() }
  const reports = data?.reports

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<div className="flex items-center gap-s">
          <IconButton icon={ArrowLeft} label="Back to knowledge" size={40} onClick={onBack} />
          <PageTitle>Scheduled reports</PageTitle>
        </div>}
        right={
          <HeaderActions>
            <HeaderControl icon={Plus} label="New report" variant="primary" priority="primary"
              hint="Watch a corner of the library on a schedule" onClick={() => setCreating(true)} />
          </HeaderActions>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {creating && (
            <div className="mb-l">
              <CreateForm onCreated={() => { setCreating(false); reload() }} onCancel={() => setCreating(false)} />
            </div>
          )}
          {
}
          {reports === undefined && error ? (
            <LoadError what="scheduled reports" error={error} onRetry={reload} />
          ) : reports === undefined || loading ? (
            <ListSkeleton rows={3} what="scheduled reports" />
          ) : reports.length === 0 ? (
            <EmptyState icon={FileClock} title="No scheduled reports"
              hint="A report watches part of your library on a schedule and writes what it finds back in as a knowledge item."
              action={{ label: 'New report', onClick: () => setCreating(true), icon: Plus }} />
          ) : (
            <div className="flex flex-col gap-m">
              {reports.map((r, i) => <ReportRow key={r.id} report={r} index={i} onChanged={reload} />)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
