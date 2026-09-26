import { useEffect, useRef, useState } from 'react'
import { toneChipSkin } from '../../shared/theme/accent'
import { FieldError } from '../../shared/ui/forms'
import { Pencil, Trash2, Check, X, PlayCircle, Loader2, MessagesSquare, ChevronRight, AlertTriangle, FlaskConical } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { FormFooter } from '../../shared/ui/FormFooter'
import { TextLink } from '../../shared/ui/TextLink'
import { Toggle } from '../../shared/ui/Toggle'
import { InvestigateButton } from '../../shared/ui/InvestigateButton'
import { Markdown } from '../../shared/ui/Markdown'
import { confirmDelete } from '../../shared/ui/dialog'
import { api, type ScheduleJob, type ScheduleRun } from '../../shared/data/api'
import { kindMeta, modeMeta, deriveKind, deriveMode, statusMeta, lastRunMeta, isInertOutcome, relFuture, relPast, absTime, mdToPlain } from './scheduleMeta'
import { actionLabel, actionIcon } from '../triggers/triggerMeta'
import { ScheduleForm, toDraft, draftToPayload, type ScheduleDraft } from './ScheduleForm'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function ScheduleDetail({ job, onSaved, onDeleted, onChanged, editing, onEditingChange }: {
  job: ScheduleJob
  onSaved: () => void
  onDeleted: () => void
  onChanged: () => void
  editing: boolean
  onEditingChange: (v: boolean) => void
}) {
  const setEditing = onEditingChange
  const [draft, setDraft] = useState<ScheduleDraft>(() => toDraft(job))
  const [saving, setSaving] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')
  const [histKey, setHistKey] = useState(0)

  const [triggered, setTriggered] = useState(false)
  const [ranFlash, setRanFlash] = useState<null | 'ok' | 'error'>(null)
  const [fading, setFading] = useState(false)
  const runStartRef = useRef<number | null>(null)
  const km = kindMeta(deriveKind(job))
  const mm = modeMeta(deriveMode(job))
  const provider = job.action?.provider
  const cfg = (job.action?.config ?? {}) as Record<string, unknown>
  const ActionIcon = provider ? actionIcon(provider) : mm.icon
  const actLabel = provider ? actionLabel(provider) : mm.label
  const running = job.is_running || triggered

  useEffect(() => { setDraft(toDraft(job)) }, [job.id])

  useEffect(() => {
    if (!triggered) return
    const finished = !job.is_running && job.last_run_ts != null && job.last_run_ts !== runStartRef.current
    if (finished) {
      setTriggered(false)
      setHistKey((k) => k + 1)
      setRanFlash(job.last_status === 'error' ? 'error' : 'ok')
      return
    }
    const t = window.setInterval(() => onChanged(), 2500)
    return () => clearInterval(t)
  }, [triggered, job.is_running, job.last_run_ts])

  useEffect(() => {
    if (!ranFlash) return
    setFading(false)
    const fade = window.setTimeout(() => setFading(true), 2200)
    const clear = window.setTimeout(() => { setRanFlash(null); setFading(false) }, 2700)
    return () => { clearTimeout(fade); clearTimeout(clear) }
  }, [ranFlash])

  async function save() {
    if (!draft.name.trim()) { setErr('Name is required'); return }
    setSaving(true); setErr('')
    try { await api.updateSchedule(job.id, draftToPayload(draft)); onSaved(); setEditing(false) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Save failed') } finally { setSaving(false) }
  }
  async function del() {
    if (!(await confirmDelete('schedule', job.name, { body: 'Its run history is removed too. This cannot be undone.' }))) return
    try { await api.deleteSchedule(job.id); onDeleted() } catch { setErr('Delete failed') }
  }
  async function runNow() {
    setBusy(true); setErr(''); setNote('')
    runStartRef.current = job.last_run_ts ?? null
    try {
      const r = await api.runSchedule(job.id)
      if (r.ok === false) {
        setErr(r.refused || (typeof r.result === 'string' && r.result) || 'This schedule did not run.')
        return
      }
      setTriggered(true)
      onChanged()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Run failed'
      setErr(/already running/i.test(msg) ? 'This schedule is already running.' : msg)
    } finally { setBusy(false) }
  }
  async function dryRun() {
    setBusy(true); setErr(''); setNote('')
    try {
      const result = await api.runSchedule(job.id, true)
      if (!result.ok) { setErr(result.refused || 'Could not preview this schedule'); return }
      const plan = (result.result as { plan?: { enforced?: string[]; bypassed?: string[] } } | undefined)?.plan
      setNote(plan
        ? `Dry run: no action executed. Enforced gates: ${(plan.enforced ?? []).join(', ') || 'none'}. Manual bypasses: ${(plan.bypassed ?? []).join(', ') || 'none'}.`
        : 'Dry run: no action executed.')
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Dry run failed'
      setErr(/already running/i.test(msg) ? 'This schedule is already running.' : msg)
    } finally { setBusy(false) }
  }
  async function toggle() {
    setBusy(true)
    try { await api.enableSchedule(job.id, !job.enabled); onChanged() }
    catch (e) { setErr(e instanceof Error ? e.message : (job.enabled ? 'Disable failed' : 'Enable failed')) }
    finally { setBusy(false) }
  }
  async function openChat() {
    setBusy(true); setNote('')
    try { const r = await api.scheduleToChat(job.id); if (r?.session) setNote(`Opened as chat session "${r.session}" — find it in Chat.`) }
    catch (e) { setErr(e instanceof Error ? e.message : 'Open chat failed') } finally { setBusy(false) }
  }

  if (editing) {
    return (
      <div className="flex flex-col gap-l">
        <ScheduleForm draft={draft} onChange={setDraft} compact />
        {err && <FieldError>{err}</FieldError>}
        <FormFooter>
          <Button variant="ghost" size="sm" onClick={() => { setDraft(toDraft(job)); setEditing(false); setErr('') }}><X size={15} /> Cancel</Button>
          <Button size="sm" onClick={save} loading={saving} disabled={saving || !draft.name.trim()}
            disabledReason={!draft.name.trim() ? 'Enter a name first' : undefined}><Check size={15} /> Save</Button>
        </FormFooter>
      </div>
    )
  }

  const ss = lastRunMeta(job.last_run_status, job.last_status)
  return (
    <div className="flex flex-col gap-l">
      { }
      <div className="flex flex-wrap items-center gap-s">
        {
}
        <Button size="sm" variant="secondary" onClick={runNow} disabled={busy || running || !!ranFlash}
          disabledReason={ranFlash ? undefined : BUSY_REASON}>
          <span className={`inline-flex items-center gap-1.5 transition-opacity duration-500 ${fading ? 'opacity-0' : 'opacity-100'}`}
            style={ranFlash === 'ok' ? { color: 'var(--color-ok)' } : ranFlash === 'error' ? { color: 'var(--color-danger)' } : undefined}>
            {running ? <Loader2 size={14} className="animate-spin" />
              : ranFlash === 'ok' ? <Check size={14} />
              : ranFlash === 'error' ? <AlertTriangle size={14} />
              : <PlayCircle size={14} />}
            {running ? 'Running…' : ranFlash === 'ok' ? 'Run finished' : ranFlash === 'error' ? 'Run failed' : 'Run now'}
          </span>
        </Button>
        <span title="Dry-run replay — preview what this would do, with no side effects (write tools are not executed)">
          <Button size="sm" variant="ghost" onClick={dryRun} disabled={busy || running || !!ranFlash}
            disabledReason={ranFlash ? undefined : BUSY_REASON}>
            <FlaskConical size={14} /> Dry run
          </Button>
        </span>
        <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil size={14} /> Edit</Button>
        {job.has_result && <Button size="sm" variant="ghost" onClick={openChat} disabled={busy} disabledReason={BUSY_REASON}><MessagesSquare size={14} /> Open as chat</Button>}
        <Button size="sm" variant="ghost" onClick={del}><Trash2 size={14} /> Delete</Button>
        <label className="ml-auto inline-flex items-center gap-2 text-[0.8125rem] cursor-pointer">
          <span className="text-on-surface-var">{job.enabled ? 'Enabled' : 'Disabled'}</span>
          <Toggle on={job.enabled} onChange={toggle} disabled={busy} label="Toggle enabled" size="sm" />
        </label>
      </div>
      {err && <FieldError>{err}</FieldError>}
      {note && !running && <p className="text-ok text-[0.8125rem]">{note}</p>}

      {
}
      { }
      <div className="flex flex-wrap items-center gap-s">
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={toneChipSkin(km.tone, 16)}><km.icon size={13} /> {job.schedule}</span>
        <span className="inline-flex items-center gap-1.5 rounded-pill px-m h-7 text-[0.8125rem]" style={toneChipSkin(mm.tone, 16)}><ActionIcon size={13} /> {actLabel}</span>
        {job.enabled && job.next_run_ts && <span className="text-on-surface-low text-[0.8125rem]">next {relFuture(job.next_run_ts)} · {absTime(job.next_run_ts)}</span>}
      </div>

      { }
      {provider === 'run-prompt' ? (
        <Section label="Prompt">
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] font-mono break-words">
            {String(cfg.prompt_id || '') || <span className="text-on-surface-low">loop.md (default recurring prompt)</span>}
          </div>
        </Section>
      ) : provider === 'run-workflow' ? (
        <Section label="Workflow">
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] font-mono break-words">
            {String(cfg.workflow_id || '—')}
          </div>
        </Section>
      ) : (
        <Section label={mm.key === 'agent' ? 'Prompt' : mm.key === 'script' ? 'Script' : 'Command'}>
          <div className="rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] leading-relaxed whitespace-pre-wrap break-words font-mono">
            {mm.key === 'agent' ? (job.message || '—') : mm.key === 'script' ? job.script : job.command}
          </div>
          {mm.key === 'agent' && (job.agent || job.model) && (
            <div className="mt-1.5 flex flex-wrap gap-1.5 text-[0.75rem]">
              {job.agent && <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{job.agent}</span>}
              {job.model && <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{job.model}</span>}
            </div>
          )}
        </Section>
      )}

      { }
      {(job.timezone || job.channel || job.silent || job.strict_schedule || (job.skip_dates?.length ?? 0) > 0) && (
        <div className="flex flex-wrap gap-1.5 text-[0.75rem]">
          {job.timezone && <Chip>{job.timezone}</Chip>}
          {job.channel && <Chip>↳ {job.channel}</Chip>}
          {job.silent && <Chip>silent</Chip>}
          {job.strict_schedule && <Chip>strict</Chip>}
          {(job.skip_dates?.length ?? 0) > 0 && <Chip>{job.skip_dates!.length} skip date{job.skip_dates!.length > 1 ? 's' : ''}</Chip>}
        </div>
      )}

      { }
      <Section label="Last run">
        <div className="flex items-center gap-2 text-[0.8125rem]">
          <ss.icon size={15} style={{ color: ss.tone }} />
          <span className="text-on-surface-var">{job.last_run_ts ? `${ss.label} · ${relPast(job.last_run_ts)}` : 'never run'}</span>
        </div>
        {job.last_error && <div className="mt-2 rounded-md px-m py-2 text-[0.8125rem]" style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}><AlertTriangle size={13} className="inline mr-1" />{job.last_error}</div>}
        {job.last_result && <div className="mt-2 rounded-md bg-surface-container px-m py-2 text-on-surface-var text-[0.8125rem] leading-relaxed"><Markdown>{job.last_result}</Markdown></div>}
      </Section>

      <RunHistory triggerId={`schedule:${job.id}`} reloadKey={histKey} />
    </div>
  )
}

function Chip({ children }: { children: React.ReactNode }) {
  return <span className="rounded-pill bg-surface-high px-2 h-6 inline-flex items-center text-on-surface-var font-mono">{children}</span>
}

export function RunHistory({ triggerId, reloadKey = 0 }: { triggerId: string; reloadKey?: number }) {
  const [runs, setRuns] = useState<ScheduleRun[] | null>(null)
  const [total, setTotal] = useState(0)
  const [limit, setLimit] = useState(5)
  const [openRun, setOpenRun] = useState<string | null>(null)
  const [unsupported, setUnsupported] = useState<string | null>(null)
  const rawId = triggerId.replace(/^(?:schedule|store|lifecycle|event):/, '')

  useEffect(() => {
    let alive = true
    api.triggerHistory(triggerId, limit).then((d) => {
      if (!alive) return
      setUnsupported(d.supported === false ? (d.reason || 'this kind keeps no run records') : null)
      setRuns(d.runs); setTotal(d.total)
    }).catch(() => { if (alive) setRuns([]) })
    return () => { alive = false }
  }, [triggerId, limit, reloadKey])

  if (runs === null) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">Loading…</div></Section>
  if (unsupported) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">{unsupported}</div></Section>
  if (runs.length === 0) return <Section label="History"><div className="text-on-surface-low text-[0.8125rem]">No runs recorded yet.</div></Section>

  return (
    <Section label={`History · ${total}`}>
      <div className="flex flex-col gap-1">
        {runs.map((r, i) => {
          const id = r.run_id ?? String(i)
          const sm = statusMeta(r.status)
          const expanded = openRun === id
          return (
            <div key={id} className="rounded-md bg-surface-container overflow-hidden">
              <button type="button" aria-expanded={expanded} onClick={() => setOpenRun(expanded ? null : id)}
                className="flex w-full items-center gap-s px-m py-2 text-left hover:bg-surface-high transition-colors">
                <ChevronRight size={14} className={`shrink-0 text-on-surface-low transition-transform ${expanded ? 'rotate-90' : ''}`} />
                <sm.icon size={14} style={{ color: sm.tone }} className="shrink-0" />
                <span className="flex-1 truncate text-on-surface text-[0.8125rem]">{mdToPlain(r.summary || r.error) || sm.label}</span>
                {r.trigger === 'manual' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-on-surface-low text-[0.75rem]">manual</span>}
                {r.trigger === 'replay' && <span className="shrink-0 rounded-pill bg-surface-high px-1.5 text-info text-[0.75rem]">dry run</span>}
                <span className="shrink-0 text-on-surface-low text-[0.75rem]">{relPast(r.started_at ?? r.finished_at)}</span>
              </button>
              {
}
              {r.run_id && (
                <div className="flex justify-end px-m pb-1.5" onClick={(e) => e.stopPropagation()}>
                  {
}
                  <InvestigateButton kind="schedule_run" id={`${rawId}:${r.run_id}`}
                    backLink={`#/triggers?open=${triggerId}`} size={28} />
                </div>
              )}
              {expanded && <RunTrace triggerId={triggerId} runId={id} preview={r} />}
            </div>
          )
        })}
      </div>
      {runs.length < total && (
        <TextLink onClick={() => setLimit((l) => l + 10)} size="sm" className="mt-1.5">Show more ({total - runs.length} more)</TextLink>
      )}
    </Section>
  )
}

function RunTrace({ triggerId, runId, preview }: { triggerId: string; runId: string; preview: ScheduleRun }) {
  const [run, setRun] = useState<ScheduleRun | null>(preview.trace ? preview : null)
  useEffect(() => {
    if (run) return
    let alive = true
    api.triggerRunDetail(triggerId, runId).then((r) => { if (alive) setRun(r) }).catch(() => { if (alive) setRun(preview) })
    return () => { alive = false }
  }, [triggerId, runId])
  if (!run) return <div className="px-m pb-2 text-on-surface-low text-[0.75rem]">Loading trace…</div>
  const inert = isInertOutcome(run.outcome ?? run.status)
  return (
    <div className="px-m pb-3 flex flex-col gap-2 text-[0.8125rem]">
      <div className="flex flex-wrap gap-x-m gap-y-0.5 text-on-surface-low text-[0.75rem]">
        {run.started_at && <span>started {absTime(run.started_at)}</span>}
        {run.finished_at && <span>· finished {absTime(run.finished_at)}</span>}
        {run.duration_ms != null && <span>· {(run.duration_ms / 1000).toFixed(1)}s</span>}
      </div>
      {
}
      {run.error && (
        <div
          className="rounded-md px-m py-1.5"
          style={
            inert
              ? { background: 'var(--color-surface)', color: 'var(--color-on-surface-low)' }
              : { background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }
          }
        >{run.error}</div>
      )}
      {
}
      {(run.trace || run.summary) && <div className="rounded-md bg-surface px-m py-2 text-on-surface-var leading-relaxed"><Markdown>{run.trace || run.summary || ''}</Markdown></div>}
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return <div><div className="text-on-surface-low text-[0.75rem] uppercase tracking-wide mb-1.5">{label}</div>{children}</div>
}
