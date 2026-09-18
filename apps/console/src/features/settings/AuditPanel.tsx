import { useCallback, useEffect, useRef, useState } from 'react'
import { Search, RefreshCw, ShieldCheck, ShieldAlert, Archive, Download, SlidersHorizontal } from 'lucide-react'
import { api, type AuditFilters, type AuditPage, type SelEvent, type SelVerify } from '../../shared/data/api'
import { invalidateKeys } from '../../shared/data/data'
import { confirm } from '../../shared/ui/dialog'
import { InvestigateButton } from '../../shared/ui/InvestigateButton'
import { PanelHeader } from './settingsUI'
import { Button } from '../../shared/ui/Button'
import { ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Field, TextInput, DateInput } from '../../shared/ui/forms'
import { notify } from '../../app/shell/appSdk'

const OUTCOME_TONE: Record<string, string> = {
  success: 'var(--color-success)', allowed: 'var(--color-success)', approved: 'var(--color-success)',
  completed: 'var(--color-success)', ok: 'var(--color-success)',
  denied: 'var(--color-danger)', failure: 'var(--color-danger)', failed: 'var(--color-danger)',
  blocked: 'var(--color-danger)', refused: 'var(--color-danger)', rejected: 'var(--color-danger)',
  error: 'var(--color-danger)',
  not_triggered: 'var(--color-on-surface-low)', scanned: 'var(--color-on-surface-low)',
  needs_confirm: 'var(--color-warning)',
}

const ALL_PRESET = { key: '', label: 'All', values: [] as string[] }

const PAGE_SIZE = 50

export function toJsonl(events: SelEvent[]): string {
  return events.map((e) => JSON.stringify(e)).join('\n') + (events.length ? '\n' : '')
}

function downloadJsonl(events: SelEvent[]): void {
  const url = URL.createObjectURL(new Blob([toJsonl(events)], { type: 'application/x-ndjson' }))
  const a = document.createElement('a')
  a.href = url
  a.download = `gideon-audit-${new Date().toISOString().slice(0, 10)}.jsonl`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
}

export function verifiedScope(v: { checked: number; windowed?: boolean; window?: number | null }): string {
  const n = v.checked.toLocaleString()
  return capped(v) ? `the last ${n} events` : `all ${n} events`
}

export function capped(v: { checked: number; windowed?: boolean; window?: number | null }): boolean {
  return !!v.windowed && typeof v.window === 'number' && v.checked >= v.window
}

export function AuditPanel() {
  const [filters, setFilters] = useState<AuditFilters>({})
  const [showMore, setShowMore] = useState(false)
  const [events, setEvents] = useState<SelEvent[] | null>(null)
  const [cursor, setCursor] = useState('')
  const [truncated, setTruncated] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [verify, setVerify] = useState<SelVerify | null>(null)
  const [families, setFamilies] = useState<AuditPage['outcome_families']>([])

  const runId = useRef(0)

  const load = useCallback(async (opts: { cursor?: string; filters: AuditFilters }) => {
    const id = ++runId.current
    setBusy(true)
    try {
      const page = await api.auditEvents({ limit: PAGE_SIZE, cursor: opts.cursor, filters: opts.filters })
      if (id !== runId.current) return
      setEvents((prev) => (opts.cursor && prev ? [...prev, ...page.events] : page.events))
      if (page.outcome_families?.length) setFamilies(page.outcome_families)
      setCursor(page.next_cursor)
      setTruncated(page.truncated)
      setError(null)
    } catch (e) {
      if (id !== runId.current) return
      setError(e)
    } finally {
      if (id === runId.current) setBusy(false)
    }
  }, [])

  useEffect(() => {
    const t = setTimeout(() => { void load({ filters }) }, 300)
    return () => clearTimeout(t)
  }, [filters, load])

  const setFilter = (k: keyof AuditFilters, v: string) => setFilters((f) => ({ ...f, [k]: v }))
  const presets = [ALL_PRESET, ...families]
  const reload = () => { setEvents(null); void load({ filters }) }
  const loadMore = () => { void load({ cursor, filters }) }

  const runVerify = async () => {
    setVerify(null)
    try { setVerify(await api.auditVerify()) } catch { setVerify({ ok: false, checked: 0, error: 'verify failed' }) }
  }
  const rotate = async () => {
    if (!(await confirm({ title: 'Archive the audit log and start a new chain?', body: 'The existing entries move to a timestamped archive file next to the log — they leave the dashboard verify and browse surface. The signing key is unchanged.', confirmLabel: 'Archive & reset' }))) return
    try {
      const res = await api.selRotate()
      const archived = res.archive_path ? res.archive_path.split(/[/\\]/).pop() : ''
      notify(archived ? `Audit log archived to ${archived} — a fresh chain has started.` : 'Audit log reset — a fresh chain has started.', 'success')
    }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'the request failed'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch {   }
      notify(`Couldn't archive the audit log: ${msg}`, 'error')
      return
    }
    invalidateKeys('settings:audit-verify')
    reload()
  }

  if (!events && error) return <LoadError what="audit log" error={error} onRetry={reload} />
  if (!events) return <ListSkeleton rows={8} what="audit log" />

  const broken = events.filter((e) => e.integrity_ok === false).length

  return (
    <div>
      <PanelHeader title="Audit log" hint="What your agent did — every tool call, approval, denial, and redaction, hash-chained and tamper-evident." />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-pill bg-surface-container p-0.5" role="group" aria-label="Filter by outcome">
          {presets.map((f) => {
            const sent = f.values.join(',')
            const active = (filters.outcome ?? '') === sent
            return (
              <button key={f.key || 'all'} type="button" onClick={() => setFilter('outcome', sent)} aria-pressed={active}
                title={f.values.length ? `Outcomes: ${f.values.join(', ')}` : undefined}
                data-type="body-s" className="rounded-pill px-3 h-7 transition-colors"
                style={active ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>{f.label}</button>
            )
          })}
        </div>
        <div className="min-w-40 flex-1">
          <TextInput value={filters.operation ?? ''} onChange={(v) => setFilter('operation', v)} placeholder="Filter by operation" ariaLabel="Filter by operation"
            size="md" surface="high" leadingIcon={<Search size={14} />} />
        </div>
        {
}
        <Button variant="secondary" size="sm" onClick={() => setShowMore((s) => !s)} ariaExpanded={showMore}><SlidersHorizontal size={14} /> Filters</Button>
        { }
        <Button variant="secondary" size="sm" onClick={reload} loading={busy} title={busy ? 'Refreshing the audit log' : 'Refresh the audit log'}><RefreshCw size={14} /></Button>
        <Button variant="secondary" size="sm" onClick={runVerify}><ShieldCheck size={14} /> Verify</Button>
        {
}
        <Button variant="secondary" size="sm" onClick={() => downloadJsonl(events)} disabled={!events.length}
          disabledReason="Nothing to export — no events match the current filters"
          title={`Export the ${events.length} listed events as JSONL (credential-safe)`}><Download size={14} /> Export</Button>
        <Button variant="ghost" size="sm" onClick={rotate}><Archive size={14} /> Rotate</Button>
      </div>

      {showMore && (
        <div className="mb-3 grid gap-3 rounded-lg bg-surface-container p-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Caller"><TextInput value={filters.caller ?? ''} onChange={(v) => setFilter('caller', v)} placeholder="session key" size="md" surface="high" /></Field>
          <Field label="Downstream service"><TextInput value={filters.downstream_service ?? ''} onChange={(v) => setFilter('downstream_service', v)} placeholder="MCP server" size="md" surface="high" /></Field>
          <Field label="From"><DateInput value={filters.since ?? ''} onChange={(v) => setFilter('since', v)} /></Field>
          <Field label="To"><DateInput value={filters.until ?? ''} onChange={(v) => setFilter('until', v)} /></Field>
        </div>
      )}

      {verify && (
        <div data-type="body-s" className="mb-3 rounded-lg bg-surface-container px-3 py-2">
          <div className="flex items-center gap-1.5"
            style={{ color: verify.ok ? 'var(--color-success)' : 'var(--color-danger)' }}>
            {verify.ok ? <ShieldCheck size={14} /> : <ShieldAlert size={14} />}
            {verify.ok
              ? `Chain intact — ${verifiedScope(verify)} verified.`
              : `Chain broken — ${verify.tampered ?? '?'} of ${verifiedScope(verify)} altered${verify.error ? ` (${verify.error})` : ''}.`}
          </div>
          {capped(verify) && (
            <p data-type="caption" className="mt-1 text-on-surface-low">
              Older entries were not checked — this is the live tamper-detection window.
              {' '}<code className="font-mono">gideon security verify</code> walks the whole log
              offline, which can take a while on a long one.
            </p>
          )}
        </div>
      )}

      {
}
      {broken > 0 && (
        <div role="alert" data-type="body-s" className="mb-3 flex items-center gap-1.5 rounded-lg px-3 py-2"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <ShieldAlert size={14} />
          {broken === 1 ? '1 listed event fails its integrity check — it was altered on disk.' : `${broken} listed events fail their integrity check — they were altered on disk.`}
        </div>
      )}

      {events.length === 0 ? (
        <p data-type="body-s" className="py-6 text-center text-on-surface-low">No matching events.</p>
      ) : (
        <div className="flex flex-col gap-1">
          {events.map((e) => <EventRow key={e.event_id} ev={e} />)}
        </div>
      )}

      <div className="mt-3 flex flex-col items-center gap-1.5">
        {cursor && (
          <Button variant="secondary" size="sm" onClick={loadMore} loading={busy} loadingLabel="Loading">Load older events
          </Button>
        )}
        {!cursor && events.length > 0 && (
          <p data-type="caption" className="text-on-surface-low">
            {truncated ? `End of the ${events.length} most recent matching events — older entries exist beyond the scanned window.` : `All ${events.length} matching events shown.`}
          </p>
        )}
      </div>
    </div>
  )
}

function EventRow({ ev }: { ev: SelEvent }) {
  const [open, setOpen] = useState(false)
  const tone = OUTCOME_TONE[ev.outcome ?? ''] ?? 'var(--color-on-surface-low)'
  const tampered = ev.integrity_ok === false
  return (
    <div className="rounded-md px-3 py-1.5" style={tampered
      ? { background: 'color-mix(in srgb, var(--color-danger) 16%, var(--color-surface-container))' }
      : { background: 'var(--color-surface-container)' }}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} data-type="caption" className="flex w-full items-center gap-2 text-left">
        <span data-type="caption" className="w-14 shrink-0 font-mono" style={{ color: tone }}>{ev.outcome || '—'}</span>
        <span data-type="caption" className="shrink-0 rounded bg-surface-high px-1.5 text-on-surface-low">{ev.event_type}</span>
        <span className="min-w-0 flex-1 truncate text-on-surface">{ev.operation || ev.resources || '—'}</span>
        { }
        {tampered && <ShieldAlert size={13} className="shrink-0" style={{ color: 'var(--color-danger)' }} aria-label="Integrity check failed — this record was altered" />}
        <span data-type="caption" className="shrink-0 text-on-surface-low">{fmtTime(ev.timestamp)}</span>
      </button>
      {open && (
        <>
          {tampered && (
            <p data-type="caption" className="mt-1.5" style={{ color: 'var(--color-danger)' }}>
              This record's HMAC does not match its contents — it was modified after it was written.
            </p>
          )}
          <div data-type="caption" className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-0.5 border-t border-outline-variant/30 pt-1.5">
            <Kv k="caller" v={ev.caller_identity} /><Kv k="agent" v={ev.agent} />
            <Kv k="source" v={ev.source} /><Kv k="tool kind" v={ev.tool_kind} />
            <Kv k="downstream" v={ev.downstream_service} />
            {ev.resources && <Kv k="resources" v={ev.resources} span />}
            {ev.metadata?.reason && <Kv k="reason" v={String(ev.metadata.reason)} span />}
            {ev.error && <Kv k="error" v={ev.error} span />}
          </div>
          {
}
          <div className="mt-1 flex justify-end">
            <InvestigateButton kind="audit_event" id={ev.event_id} backLink="#/settings/security" size={28} />
          </div>
        </>
      )}
    </div>
  )
}
function Kv({ k, v, span }: { k: string; v?: string; span?: boolean }) {
  if (!v) return null
  return <div className={span ? 'col-span-2' : ''}><span className="text-on-surface-low">{k}: </span><span className="font-mono text-on-surface">{v}</span></div>
}
function fmtTime(iso?: string): string {
  if (!iso) return ''
  const m = iso.match(/[T ](\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : iso.slice(11, 19)
}
