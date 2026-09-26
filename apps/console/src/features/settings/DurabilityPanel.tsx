import { useEffect, useRef, useState } from 'react'
import { epochSeconds } from '../../shared/data/epoch'
import { AlertTriangle, History, HardDriveDownload, ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import {
  api,
  type DurabilityArchive,
  type DurabilityArchives,
  type DurabilityConflict,
  type DurabilityConflictChoice,
  type DurabilityConflicts,
  type DurabilityHistoryDiffFile,
  type DurabilityHistoryEntry,
  type DurabilityHistoryPreview,
  type DurabilityStatus,
  type SettingsProvider,
} from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, Row, Toggle, ToggleRow, SavedToast } from './settingsUI'
import { Checkbox, NumberField, Select } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'
import { fvs } from '../../shared/theme/fontWeight'
import { confirm } from '../../shared/ui/dialog'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function DurabilityPanel() {
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null)
  const [surface, setSurface] = useState(SURFACE_DURABILITY)

  const { data, error: loadErr, refresh } = useQuery(`settings:durability:${surface}`, async () => {
    const [plaw, status, snaps, conflicts, transports] = await Promise.all([
      api.gideonConfig(),
      api.durabilityStatus().catch(() => null),
      api.durabilityArchive().catch(() => null),
      settle(api.durabilityConflicts(surface)),
      settle(api.settingsProviders().then((ps) => ps.filter((p) => p.provider?.type === 'sync'))),
    ])
    return {
      durability: (plaw.durability ?? {}) as Record<string, unknown>,
      status,
      snaps,
      conflicts,
      transports,
    }
  }, { persist: true })

  useEffect(() => { if (data) setCfg(data.durability) }, [data])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={2} what="settings" />

  return (
    <div>
      <PanelHeader
        title="Backups"
        hint="What gets backed up automatically, how long copies are kept, and whether a restore is ever actually rehearsed." />
      <ScheduleSection cfg={cfg} setCfg={setCfg} status={data.status} onChanged={refresh} />
      <RetentionSection cfg={cfg} setCfg={setCfg} snaps={data.snaps} />
      <ArchiveSection snaps={data.snaps} onChanged={refresh} />
      <TimeTravelSection cfg={cfg} setCfg={setCfg} />
      <SyncSection cfg={cfg} setCfg={setCfg} status={data.status} transports={data.transports} />
      <ConflictsSection key={surface} read={data.conflicts} surface={surface} onSurface={setSurface} onChanged={refresh} />
    </div>
  )
}

type Settled<T> = { ok: true; value: T } | { ok: false; error: string }

function settle<T>(p: Promise<T>): Promise<Settled<T>> {
  return p.then(
    (value) => ({ ok: true as const, value }),
    (e: unknown) => ({ ok: false as const, error: e instanceof Error ? e.message : String(e) }),
  )
}



function TimeTravelSection({ cfg, setCfg }: {
  cfg: Record<string, unknown>
  setCfg: (c: Record<string, unknown>) => void
}) {
  const patch = usePatch(cfg, setCfg, () => {})
  const on = cfg.time_travel !== false
  const [root, setRoot] = useState('config')
  const [sleptOnly, setSleptOnly] = useState(false)
  const [pending, setPending] = useState<Pending | null>(null)
  const [busy, setBusy] = useState(false)
  const [selected, setSelected] = useState<string[]>([])
  const [applyErr, setApplyErr] = useState('')
  const wanted = useRef<string[]>([])

  const reset = () => { setPending(null); setSelected([]); setApplyErr(''); wanted.current = [] }

  const status = useQuery('settings:history', () => api.durabilityHistory(), { persist: false })
  const timeline = useQuery(
    `settings:history:${root}:${sleptOnly ? 'slept' : 'all'}`,
    () => api.durabilityHistoryTimeline(root, { limit: 30, unattended: sleptOnly }),
    { persist: false },
  )

  const hint = 'A local, continuous history of the things you and the assistant edit — so a bad edit is an undo, not a restore. It stays on this machine: it is never synced, exported, or included in a backup, and it never records secrets.'

  const takePreview = async (entry: DurabilityHistoryEntry, op: 'rollback' | 'revert') => {
    setBusy(true)
    setApplyErr('')
    setSelected([])
    try {
      const r = await api.durabilityHistoryPreview(root, op, entry.sha)
      setPending({
        entry, op, head: r.expected_head, preview: r.preview,
        paths: r.preview.paths ?? [], files: r.preview.files,
      })
    } catch (e) {
      notify(`Couldn't read what that would change: ${String((e as Error)?.message || e)}`, 'error')
    }
    setBusy(false)
  }

  const reprev = async (paths: string[]) => {
    if (!pending) return
    const { entry, op } = pending
    setBusy(true)
    setApplyErr('')
    try {
      const r = await api.durabilityHistoryPreview(root, op, entry.sha, paths)
      if (wanted.current !== paths) return
      setPending((p) => (p ? { ...p, head: r.expected_head, preview: r.preview, paths: r.preview.paths ?? paths } : p))
    } catch (e) {
      setApplyErr(errorText(e))
      notify(`Couldn't read what that would change: ${errorText(e)}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const toggleFile = (path: string, ticked: boolean) => {
    const next = ticked ? [...selected, path] : selected.filter((p) => p !== path)
    setSelected(next)
    wanted.current = next
    void reprev(next)
  }

  const apply = async () => {
    if (!pending) return
    const { entry, op, head, paths } = pending
    if (!(await confirm(confirmCopy(op, root, entry, pending)))) return
    setBusy(true)
    setApplyErr('')
    try {
      const r = await (paths.length
        ? api.durabilityHistoryApply(root, op, entry.sha, head, paths)
        : api.durabilityHistoryApply(root, op, entry.sha, head))
      notify(
        r.reload_required
          ? 'Done. Restart Gideon for the change to take effect everywhere.'
          : 'Done.',
        'success',
      )
      reset()
      timeline.refresh()
      status.refresh()
    } catch (e) {
      setApplyErr(errorText(e))
      notify(`Nothing was changed: ${errorText(e)}`, 'error')
    }
    setBusy(false)
  }

  const roots = status.data?.roots ?? []
  const gitMissing = status.data ? status.data.git === false : false

  return (
    <Section title="Time travel" hint={hint} icon={History} iconTone="muted">
      <RowGroup>
        <ToggleRow
          label="Keep a local edit history"
          hint="Records configuration, skills, prompts, project context and memory notes as they change, roughly ten seconds after you stop typing. Off means no history is recorded from now on; what is already recorded is kept."
          cfg={cfg}
          field="time_travel"
          patch={patch} />
      </RowGroup>

      {gitMissing && (
        <div data-type="body-s" className="mt-3 flex items-start gap-2 rounded-lg bg-surface-container px-4 py-3" style={{ color: 'var(--color-warn)' }}>
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span>Time travel needs <code>git</code> installed, and this machine has none. Nothing is being recorded.</span>
        </div>
      )}

      {on && !gitMissing && (
        <div className="mt-3 rounded-lg bg-surface-container px-4 py-3">
          <div className="flex flex-wrap items-end gap-4">
            <label data-type="caption" className="flex min-w-0 flex-col gap-1 text-on-surface-low">
              What to look through
              <Select
                value={root}
                ariaLabel="What to look through"
                onChange={(v) => { setRoot(v); reset() }}
                options={roots.map((r) => ({
                  value: r.id,
                  label: `${r.label}${r.exists ? ` — ${r.commits} recorded change${r.commits === 1 ? '' : 's'}` : ' — nothing recorded yet'}`,
                }))} />
            </label>
            <Row label="Only what changed while I slept"
              hint="Changes made by scheduled or background work, rather than by you at the dashboard.">
              <Toggle on={sleptOnly} onChange={setSleptOnly} label="Only what changed while I slept" />
            </Row>
          </div>

          {timeline.error ? (
            <div data-type="body-s" className="mt-3" style={{ color: 'var(--color-error)' }}>
              The history could not be read ({errorText(timeline.error)}). That is not the same
              as having no history.
            </div>
          ) : null}

          {timeline.data && timeline.data.entries.length === 0 && (
            <p data-type="body-s" className="mt-3 text-on-surface-low">
              {sleptOnly
                ? 'Nothing changed here while you were away.'
                : timeline.data.commits === 0
                  ? 'Nothing recorded here yet. The first edit you make will show up.'
                  : 'No changes match this filter.'}
            </p>
          )}

          {timeline.data && timeline.data.entries.length > 0 && (
            <ul className="mt-3 flex list-none flex-col gap-3 p-0">
              {timeline.data.entries.map((entry, i) => (
                <li key={entry.sha} className="border-outline-variant border-t pt-3 first:border-t-0 first:pt-0">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(550)}>
                      {entry.subject}
                    </span>
                    <span data-type="caption" className="shrink-0 text-on-surface-low">
                      {relativeTime(entry.at)}
                      {entry.unattended ? ' · while you were away' : ''}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {
}
                    {
}
                    {i > 0 && (
                      <Button variant="secondary" size="sm" disabled={busy} disabledReason={BUSY_REASON}
                        ariaLabel={`See going back to here: change ${i + 1} of ${timeline.data!.entries.length} — ${entry.subject}`}
                        onClick={() => takePreview(entry, 'rollback')}>
                        See going back to here
                      </Button>
                    )}
                    <Button variant="secondary" size="sm" disabled={busy} disabledReason={BUSY_REASON}
                      ariaLabel={`See undoing just this: change ${i + 1} of ${timeline.data!.entries.length} — ${entry.subject}`}
                      onClick={() => takePreview(entry, 'revert')}>
                      See undoing just this
                    </Button>
                  </div>

                  {pending?.entry.sha === entry.sha && (
                    <PreviewCard preview={pending.preview} op={pending.op} busy={busy}
                      files={pending.files} paths={pending.paths} selected={selected}
                      root={root} error={applyErr} onToggleFile={toggleFile}
                      onApply={apply} onCancel={reset} />
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Section>
  )
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : typeof e === 'string' ? e : 'unknown error'
}

type Pending = {
  entry: DurabilityHistoryEntry
  op: 'rollback' | 'revert'
  head: string
  preview: DurabilityHistoryPreview
  paths: string[]
  files: DurabilityHistoryDiffFile[]
}

function namePaths(paths: string[]): string {
  return paths.length <= 4
    ? paths.join(', ')
    : `${paths.slice(0, 3).join(', ')} and ${paths.length - 3} more`
}

export function confirmCopy(op: 'rollback' | 'revert', root: string, entry: DurabilityHistoryEntry, pending: Pending) {
  const { paths, preview, files } = pending
  const n = paths.length
  const named = namePaths(paths)
  const fileWord = files.length === 1 ? 'file' : 'files'
  const pickedWord = n === 1 ? 'file' : 'files'
  const changeWord = preview.commits_rolled_away === 1 ? 'change' : 'changes'
  if (op === 'rollback') {
    return {
      title: n === 0 ? 'Roll back to this point?' : `Roll back ${n} of ${files.length} ${fileWord} to this point?`,
      body: n === 0
        ? `Everything in ${root} goes back to how it was at "${entry.subject}". The ${preview.commits_rolled_away} ${changeWord} made since are set aside — they stay listed here, so you can come forward again. Nothing outside this history is touched, and your saved credentials are untouched.`
        : `Only the ${n} ${pickedWord} you picked go back to how they were at "${entry.subject}": ${named}. Every other file in ${root} is left exactly as it is. The ${preview.commits_rolled_away} ${changeWord} made since are set aside for those ${n} ${pickedWord} — they stay listed here, so you can come forward again. Your saved credentials are untouched.`,
      confirmLabel: 'Roll back',
      danger: true,
    }
  }
  return {
    title: n === 0 ? 'Undo just this change?' : `Undo just this change in ${n} of ${files.length} ${fileWord}?`,
    body: n === 0
      ? `This one change is undone by applying its opposite. Anything edited afterwards is kept. If a later edit touched the same lines, nothing is applied and you will be told which file blocked it.`
      : `This one change is undone by applying its opposite to the ${n} ${pickedWord} you picked: ${named}. Anything edited afterwards is kept, and so is every other file in ${root}. If a later edit touched the same lines, nothing is applied and you will be told which file blocked it.`,
    confirmLabel: 'Undo it',
    danger: false,
  }
}

function PreviewCard({ preview, op, busy, files, paths, selected, root, error, onToggleFile, onApply, onCancel }: {
  preview: DurabilityHistoryPreview
  op: 'rollback' | 'revert'
  busy: boolean
  files: DurabilityHistoryDiffFile[]
  paths: string[]
  selected: string[]
  root: string
  error: string
  onToggleFile: (path: string, ticked: boolean) => void
  onApply: () => void
  onCancel: () => void
}) {
  const n = paths.length
  const previewWord = preview.files.length === 1 ? 'file' : 'files'
  const fileWord = files.length === 1 ? 'file' : 'files'
  const changeWord = preview.commits_rolled_away === 1 ? 'change' : 'changes'
  return (
    <div className="mt-2 rounded-md bg-surface px-3 py-2">
      <p data-type="caption" className="text-on-surface-low">
        {op === 'rollback'
          ? n === 0
            ? `${preview.files.length} ${previewWord} would change, and ${preview.commits_rolled_away} later ${changeWord} would be set aside.`
            : `${n} of ${files.length} ${fileWord} would change, and ${preview.commits_rolled_away} later ${changeWord} to them would be set aside. The rest of ${root} is left alone.`
          : n === 0
            ? `${preview.files.length} ${previewWord} would change. Later edits are kept.`
            : `${n} of ${files.length} ${fileWord} would change. Later edits are kept, and so is every file you did not pick.`}
      </p>
      {preview.files.length === 0 && (
        <p data-type="caption" className="mt-1 text-on-surface-low">Nothing would change.</p>
      )}
      {
}
      {files.length > 1 && (
        <p data-type="caption" className="mt-1 text-on-surface-low">
          Tick individual files to {op === 'rollback' ? 'roll back' : 'undo'} only those. With
          nothing ticked, all {files.length} are.
        </p>
      )}
      <ul className="mt-2 flex list-none flex-col gap-2 p-0">
        {files.map((f) => (
          <li key={f.path}>
            <label className="flex min-w-0 items-center gap-2">
              {
}
              <Checkbox
                checked={selected.includes(f.path)}
                onChange={(v) => onToggleFile(f.path, v)}
                ariaLabel={`${op === 'rollback' ? 'Roll back' : 'Undo'} ${f.path}`} />
              <span data-type="caption" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(550)}>{f.path}</span>
            </label>
            {f.rendered ? (
              <pre data-type="caption" className="mt-1 max-h-48 overflow-auto rounded bg-surface-container px-2 py-1"><code>{f.diff}</code></pre>
            ) : (
              <div data-type="caption" className="mt-1 text-on-surface-low">
                Too large to show here ({formatSize(f.bytes)}).
              </div>
            )}
          </li>
        ))}
      </ul>
      {
}
      {error && (
        <div data-type="caption" className="mt-3 flex items-start gap-2" role="alert"
          style={{ color: 'var(--color-error)' }}>
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span>Nothing was changed: {error}</span>
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant={op === 'rollback' ? 'danger' : 'primary'} size="sm" disabled={busy} disabledReason={BUSY_REASON}
          onClick={onApply}>
          {op === 'rollback' ? 'Roll back' : 'Undo it'}
        </Button>
        <Button variant="secondary" size="sm" disabled={busy} disabledReason={BUSY_REASON} onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  )
}


function ScheduleSection({ cfg, setCfg, status, onChanged }: {
  cfg: Record<string, unknown>
  setCfg: (c: Record<string, unknown>) => void
  status: DurabilityStatus | null
  onChanged: () => void
}) {
  const [saved, flash] = useSavedFlash()
  const [running, setRunning] = useState('')
  const patch = usePatch(cfg, setCfg, flash)
  const autoBackup = cfg.auto_backup !== false

  const run = async (job: 'export' | 'snapshot' | 'drill', label: string) => {
    setRunning(job)
    try {
      const r = await api.durabilityRun(job)
      notify(
        r.skipped || r.detail || `${label} finished`,
        r.ok || r.skipped ? 'success' : 'error',
      )
    } catch (e) {
      notify(`${label} failed: ${String((e as Error)?.message || e)}`, 'error')
    } finally {
      onChanged()
      setRunning('')
    }
  }

  return (
    <Section title="Schedule" hint="Backups run in the background so losing work never depends on remembering to run one.">
      <RowGroup>
        <Row label="Automatic backups"
          hint="Take a nightly snapshot and an hourly incremental export in the background. Off means backups only happen when you run them by hand — below, or with `gideon backup export`.">
          <div className="flex items-center gap-2">
            <SavedToast show={saved} />
            <Toggle on={autoBackup} onChange={(v) => patch('auto_backup', v)} label="Automatic backups" />
          </div>
        </Row>
        <Row label="Monthly restore drill"
          hint="Once a month, restore the newest snapshot into a temporary directory and verify it — a backup nobody has restored is a hope, not a backup. Never touches live data.">
          <Toggle on={cfg.restore_drills !== false} onChange={(v) => patch('restore_drills', v)} label="Monthly restore drill" />
        </Row>

        {status && (
          <div className="border-t border-outline-variant py-3">
            <div data-type="caption" className="mb-2 text-on-surface-low">
              {status.enabled
                ? 'Last run of each job:'
                : 'Automatic backups are off — manual runs still appear here.'}
            </div>
            <div className="flex flex-col gap-1.5">
              <JobLine label="Incremental export" when={status.export.last_run} due={status.export.due} />
              <JobLine label="Nightly snapshot" when={status.snapshot.last_run} due={status.snapshot.due} />
              <JobLine label="Restore drill" when={status.drill.last_run} due={status.drill.due} />
            </div>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2 border-t border-outline-variant py-3">
          <span data-type="body-s" className="mr-1 text-on-surface-var">Run now:</span>
          <RunButton label="Export" icon={HardDriveDownload} busy={running === 'export'}
            disabled={!!running} onClick={() => run('export', 'Export')} />
          <RunButton label="Snapshot" icon={HardDriveDownload} busy={running === 'snapshot'}
            disabled={!!running} onClick={() => run('snapshot', 'Snapshot')} />
          <RunButton label="Verify a restore" icon={ShieldCheck} busy={running === 'drill'}
            disabled={!!running} onClick={() => run('drill', 'Restore drill')} />
        </div>
        <p data-type="caption" className="pb-3 text-on-surface-low">
          To restore, use the archive list below. A full <em>replace</em> restore stays a
          command-line action — <code>gideon restore &lt;snapshot.tar.gz&gt; --mode replace</code> — because it has to
          overwrite live state while the gateway is stopped.
        </p>
      </RowGroup>
    </Section>
  )
}


function RetentionSection({ cfg, setCfg, snaps }: {
  cfg: Record<string, unknown>
  setCfg: (c: Record<string, unknown>) => void
  snaps: DurabilityArchives | null
}) {
  const [saved, flash] = useSavedFlash()
  const patch = usePatch(cfg, setCfg, flash)
  const pruneCount = snaps?.would_prune.length ?? 0

  return (
    <Section title="How long copies are kept"
      hint="Older snapshots thin out rather than piling up: dailies become weeklies, weeklies become monthlies. 0 disables a tier.">
      <RowGroup>
        <NumberRow label="Daily snapshots" saved={saved}
          hint="How many days of nightly snapshots to keep before thinning to weeklies."
          value={num(cfg.keep_daily, 14)} min={0} max={365} suffix="days"
          onCommit={(n, l) => patch('keep_daily', n, undefined, l)} />
        <NumberRow label="Weekly snapshots" saved={saved}
          hint="How many weeks to keep one snapshot each."
          value={num(cfg.keep_weekly, 8)} min={0} max={260} suffix="weeks"
          onCommit={(n, l) => patch('keep_weekly', n, undefined, l)} />
        <NumberRow label="Monthly snapshots" saved={saved}
          hint="How many months to keep one snapshot each."
          value={num(cfg.keep_monthly, 12)} min={0} max={120} suffix="months"
          onCommit={(n, l) => patch('keep_monthly', n, undefined, l)} />

        {pruneCount > 0 && (
          <div data-type="body-s" className="border-t border-outline-variant py-3 text-on-surface-low">
            {pruneCount} of {snaps?.archives.length ?? 0} snapshots would be removed by the
            settings above on the next pass. They are struck through in the archive below.
          </div>
        )}
      </RowGroup>
    </Section>
  )
}


function ArchiveSection({ snaps, onChanged }: {
  snaps: DurabilityArchives | null
  onChanged: () => void
}) {
  const [busy, setBusy] = useState('')
  const [plan, setPlan] = useState<{ id: string; text: string } | null>(null)

  if (!snaps) {
    return (
      <Section title="Archive" hint="Every snapshot on disk, what is in it, and whether a restore from it has been verified.">
        <div data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low">
          The archive list could not be read. The backups above may still be running —
          reload to try again.
        </div>
      </Section>
    )
  }

  const preview = async (a: DurabilityArchive) => {
    setBusy(a.id); setPlan(null)
    try {
      const r = await api.durabilityArchiveRestore(a.id)
      setPlan({ id: a.id, text: JSON.stringify(r, null, 1) })
    } catch (e) {
      notify(`Couldn't read the restore plan: ${String((e as Error)?.message || e)}`, 'error')
    }
    setBusy('')
  }

  const mergeRestore = async (a: DurabilityArchive) => {
    if (!(await confirm({
      title: 'Merge this snapshot in?',
      body: `Restore "${a.name}" in MERGE mode. Anything this instance already has is kept untouched; the snapshot only fills in what is missing.`,
      confirmLabel: 'Merge-restore',
    }))) return
    setBusy(a.id)
    try {
      const r = await api.durabilityArchiveRestore(a.id, { mode: 'merge', confirm: true })
      notify(r.ok === false ? `Restore refused: ${r.error?.message ?? 'unknown reason'}` : `Merged ${a.name}`, r.ok === false ? 'error' : 'success')
      onChanged()
    } catch (e) {
      notify(`Restore failed: ${String((e as Error)?.message || e)}`, 'error')
    }
    setBusy('')
  }

  return (
    <Section title="Archive" hint="Every snapshot on disk, what is in it, and whether a restore from it has been verified.">
      <div className="rounded-lg bg-surface-container px-4 py-3">
        <DrillLine drill={snaps.last_drill} />
        {snaps.archives.length === 0 ? (
          <div data-type="body-s" className="text-on-surface-low">
            No snapshots yet. One appears after the first nightly run, or as soon as you run
            a snapshot above.
          </div>
        ) : (
          <ul className="flex list-none flex-col gap-3 p-0">
            {snaps.archives.map((a) => (
              <li key={a.id} className="border-outline-variant border-t pt-3 first:border-t-0 first:pt-0">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span data-type="label-s" className={`min-w-0 flex-1 truncate ${a.retained ? 'text-on-surface' : 'text-on-surface-low line-through'}`} style={fvs(500)}>
                    {a.name}
                  </span>
                  <span data-type="caption" className="shrink-0 text-on-surface-low">{formatSize(a.size)}</span>
                  {a.validate && <ValidateBadge ok={a.validate.ok} detail={a.validate.detail} />}
                </div>
                <DomainCounts counts={a.domains} />
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <Button variant="secondary" size="sm" ariaLabel={`Preview restore: ${a.name}`}
                    onClick={() => preview(a)} loading={busy === a.id} loadingLabel="Working…" disabled={busy !== ''}>Preview restore
                  </Button>
                  <Button variant="secondary" size="sm" ariaLabel={`Merge-restore: ${a.name}`}
                    onClick={() => mergeRestore(a)} disabled={busy !== ''} disabledReason={BUSY_REASON}>
                    Merge-restore
                  </Button>
                </div>
                {plan?.id === a.id && (
                  <pre data-type="caption" className="mt-2 max-h-64 overflow-auto rounded-md bg-surface px-3 py-2 text-on-surface-var">{plan.text}</pre>
                )}
              </li>
            ))}
          </ul>
        )}
        <p data-type="caption" className="mt-3 text-on-surface-low">
          Stored in <code>{snaps.directory}</code>. A full <em>replace</em> restore is a
          command-line action — <code>gideon restore &lt;snapshot.tar.gz&gt; --mode replace</code> — because it has
          to overwrite live state while the gateway is stopped.
        </p>
      </div>
    </Section>
  )
}


const SURFACE_DURABILITY = 'durability'
const CONFLICT_SURFACES = [
  { value: SURFACE_DURABILITY, label: 'Data and settings' },
  { value: 'memory', label: 'Memory' },
  { value: 'knowledge', label: 'Knowledge' },
]

const ENCRYPT_OPTIONS = [
  { value: 'auto', label: 'Automatic (per transport)' },
  { value: 'on', label: 'Always encrypt' },
  { value: 'off', label: 'Never encrypt' },
]

function SyncSection({ cfg, setCfg, status, transports }: {
  cfg: Record<string, unknown>
  setCfg: (c: Record<string, unknown>) => void
  status: DurabilityStatus | null
  transports: Settled<SettingsProvider[]>
}) {
  const [saved, flash] = useSavedFlash()
  const patch = usePatch(cfg, setCfg, flash)
  const syncOn = cfg.sync_enabled === true
  const chosen = String(cfg.sync_transport ?? '')
  const enabledTransports = transports.ok ? transports.value.filter((t) => t.enabled) : []

  return (
    <Section title="Sync"
      hint="Keep more than one machine in step through storage you own — a git repo, a synced folder, a bucket. There is no Gideon server in the middle.">
      <RowGroup>
        <Row label="Sync this instance"
          hint="Push this machine's changes and pull the other machines' on the schedule below. Off means nothing leaves this machine.">
          <div className="flex items-center gap-2">
            <SavedToast show={saved} />
            <Toggle on={syncOn} onChange={(v) => patch('sync_enabled', v)} label="Sync this instance" />
          </div>
        </Row>

        {
}
        {!transports.ok ? (
          <div data-type="body-s" className="border-t border-outline-variant py-3" style={{ color: 'var(--color-error)' }}>
            The installed transports could not be read ({transports.error}). Reload to try
            again — this is not the same as having none installed.
          </div>
        ) : enabledTransports.length === 0 ? (
          <div data-type="body-s" className="border-t border-outline-variant py-3 text-on-surface-low">
            No sync transport is installed and enabled yet. Install one from the Store (git-sync
            keeps a human-readable history in a repo you own; dir-sync uses any folder that
            already syncs itself), then enable it under Settings → Providers, where its own
            settings live.
          </div>
        ) : (
          <Row label="Transport"
            hint="Which installed transport carries the shards. Its own settings — repo, folder, host — live on its provider card under Settings → Providers.">
            <Select
              value={chosen}
              ariaLabel="Sync transport"
              onChange={(v) => patch('sync_transport', v)}
              options={[
                { value: '', label: 'None — sync is idle' },
                ...enabledTransports.map((t) => ({ value: t.name, label: t.displayName || t.name })),
              ]}
            />
          </Row>
        )}

        <NumberRow label="Pull no more often than" saved={saved}
          hint="The staleness window: how long this machine may go between checking the shared store. Sync is deliberately not continuous."
          value={num(cfg.sync_stale_after_secs, 900)} min={30} max={86400} suffix="seconds"
          onCommit={(n) => patch('sync_stale_after_secs', n)} />

        <Row label="Encrypt shards"
          hint="Encrypt each shard before it leaves this machine. Automatic encrypts for third-party storage (buckets, shared folders) and leaves a private git repo readable, because a diffable history is the point of using git.">
          <Select
            value={String(cfg.sync_encrypt ?? 'auto')}
            ariaLabel="Encrypt shards"
            onChange={(v) => patch('sync_encrypt', v)}
            options={ENCRYPT_OPTIONS}
          />
        </Row>

        {status?.sync && (
          <div className="border-t border-outline-variant py-3">
            <div className="flex flex-col gap-1.5">
              <JobLine label="Last sync" when={status.sync.last_run} due={status.sync.due} />
              <div data-type="body-s" className="flex items-baseline justify-between gap-3">
                <span className="text-on-surface-var">Shards leaving this machine</span>
                <span data-type="caption" className="shrink-0 text-on-surface-low">
                  {!status.sync.transport
                    ? 'no transport chosen'
                    : status.sync.encrypted
                      ? 'encrypted'
                      : 'readable by anyone with access to that store'}
                </span>
              </div>
            </div>
            <p data-type="caption" className="mt-2 text-on-surface-low">
              Credentials never sync — API keys and this instance's secrets are excluded before
              any transport sees a byte, and re-enter per machine.
            </p>
          </div>
        )}
      </RowGroup>
    </Section>
  )
}


const CHOICE_LABELS: Record<DurabilityConflictChoice, string> = {
  keep_local: 'Keep this machine’s',
  take_remote: 'Take the other machine’s',
  accept_proposal: 'Accept the drafted merge',
}

function ConflictsSection({ read, surface, onSurface, onChanged }: {
  read: Settled<DurabilityConflicts>
  surface: string
  onSurface: (surface: string) => void
  onChanged: () => void
}) {
  const [busy, setBusy] = useState('')
  const [expanded, setExpanded] = useState('')

  const hint = 'When two machines edit the same thing while apart, Gideon keeps both versions and waits for you instead of guessing.'
  const categories = <Row label="Conflict category">
    <Select value={surface} onChange={onSurface} ariaLabel="Conflict category"
      options={CONFLICT_SURFACES.map((option) => ({
        ...option,
        label: `${option.label} (${read.ok ? read.value.counts.by_surface[option.value] ?? 0 : '?'})`,
      }))} />
  </Row>

  if (!read.ok) {
    return (
      <Section title="Conflicts to review" hint={hint}>
        {categories}
        <div data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3">
          <div className="flex items-start gap-2" style={{ color: 'var(--color-error)' }}>
            <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              The review queue could not be read ({read.error}). This is <em>not</em> the same
              as having nothing to review — there may be conflicts waiting.
            </span>
          </div>
          <div className="mt-3">
            <Button variant="secondary" size="sm" onClick={onChanged}>Try again</Button>
          </div>
        </div>
      </Section>
    )
  }

  const { conflicts, counts, sync } = read.value
  const pending = conflicts.filter((c) => c.status === 'needs-review')
  const decided = conflicts.filter((c) => c.status !== 'needs-review')
  const elsewhere = Object.entries(counts.by_surface)
    .filter(([other, n]) => other !== surface && n > 0)

  const resolve = async (c: DurabilityConflict, choice: DurabilityConflictChoice) => {
    if (!(await confirm({
      title: 'Write this version?',
      body: choice === 'keep_local'
        ? `${CHOICE_LABELS[choice]} version of ${c.entity_id} will be written into ${c.entry_id} on this machine. The other machine's version stays in the shared store, so you can still decide differently from that side.`
        : `${CHOICE_LABELS[choice]} version of ${c.entity_id} will be written into ${c.entry_id} on this machine, replacing this machine's copy. That copy is not kept anywhere else — only a snapshot has it.`,
      confirmLabel: 'Write it',
      danger: true,
    }))) return
    setBusy(c.id)
    try {
      const r = await api.resolveDurabilityConflict(c.id, choice)
      notify(`Resolved ${c.entity_id}: ${CHOICE_LABELS[choice].toLowerCase()} version written (${r.written} written).`, 'success')
      onChanged()
    } catch (e) {
      notify(`Nothing was applied: ${String((e as Error)?.message || e)}`, 'error')
    }
    setBusy('')
  }

  return (
    <Section title="Conflicts to review" hint={hint}>
        {categories}
      <div className="rounded-lg bg-surface-container px-4 py-3">
        {pending.length === 0 ? (
          <div data-type="body-s" className="text-on-surface-low">
            {!sync.configured
              ? 'Nothing to review — but sync has never run on this instance, so no two versions have ever been compared. Choose a transport above first.'
              : 'Nothing to review. Every change either merged cleanly or only one machine had touched it.'}
          </div>
        ) : (
          <ul className="flex list-none flex-col gap-3 p-0">
            {pending.map((c) => (
              <li key={c.id} className="border-outline-variant border-t pt-3 first:border-t-0 first:pt-0">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(550)}>
                    {c.entity_id}
                  </span>
                  <span data-type="caption" className="shrink-0 text-on-surface-low">
                    in {c.entry_id}{c.detected_at ? ` · found ${relativeTime(c.detected_at)}` : ''}
                  </span>
                </div>
                <p data-type="caption" className="mt-1 text-on-surface-low">
                  Both machines changed this after they last agreed. This machine's version is
                  in place; nothing has been overwritten.
                </p>

                {c.proposal
                  ? (
                    <div data-type="caption" className="mt-2 rounded-md bg-surface px-3 py-2">
                      <div className="text-on-surface" style={fvs(550)}>Drafted merge</div>
                      {c.rationale && <p className="mt-1 text-on-surface-low">{c.rationale}</p>}
                    </div>
                  )
                  : (
                    <p data-type="caption" className="mt-2 text-on-surface-low">
                      {c.proposal_error
                        ? `No merge was drafted — ${c.proposal_error}. Choose a version yourself.`
                        : 'No merge has been drafted yet. Choose a version yourself, or wait for the next background pass.'}
                    </p>
                  )}

                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {
}
                  <Button variant="secondary" size="sm" loading={busy === c.id} loadingLabel="Writing…" disabled={busy !== ''}
                    ariaLabel={`${CHOICE_LABELS.keep_local}: ${c.entity_id}`}
                    onClick={() => resolve(c, 'keep_local')}>{CHOICE_LABELS.keep_local}
                  </Button>
                  <Button variant="secondary" size="sm" disabled={busy !== ''} disabledReason={BUSY_REASON}
                    ariaLabel={`${CHOICE_LABELS.take_remote}: ${c.entity_id}`}
                    onClick={() => resolve(c, 'take_remote')}>
                    {CHOICE_LABELS.take_remote}
                  </Button>
                  <Button variant="secondary" size="sm" disabled={busy !== '' || !c.proposal}
                    disabledReason={!c.proposal ? 'No merge has been drafted for this conflict' : undefined}
                    ariaLabel={`${CHOICE_LABELS.accept_proposal}: ${c.entity_id}`}
                    onClick={() => resolve(c, 'accept_proposal')}>
                    {CHOICE_LABELS.accept_proposal}
                  </Button>
                  <Button variant="secondary" size="xs" onClick={() => setExpanded(expanded === c.id ? '' : c.id)}>
                    {expanded === c.id ? 'Hide both versions' : 'Compare both versions'}
                  </Button>
                </div>

                {expanded === c.id && (
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    <RowVersion label="This machine" row={c.local_row} />
                    <RowVersion label="The other machine" row={c.remote_row} />
                    {c.proposal && <RowVersion label="Drafted merge" row={c.proposal} />}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        {elsewhere.length > 0 && (
          <div className="mt-3 flex flex-col items-start gap-2">
            <p data-type="caption" className="text-on-surface-low">
              {elsewhere.map(([other, n]) => `${n} ${other}`).join(' and ')} conflict
              {elsewhere.reduce((t, [, n]) => t + n, 0) === 1 ? '' : 's'} are waiting.
              Choose a category to compare both versions and decide which to keep.
            </p>
            {elsewhere.map(([other, n]) => (
              <Button key={other} variant="secondary" size="sm" onClick={() => onSurface(other)}>
                Review {n} {other} {n === 1 ? 'conflict' : 'conflicts'}
              </Button>
            ))}
          </div>
        )}
        {decided.length > 0 && (
          <p data-type="caption" className="mt-3 text-on-surface-low">
            {decided.length} already decided ({decided.map((c) => c.resolution).filter(Boolean).join(', ')}).
            Decisions are kept as the record of what needed one.
          </p>
        )}
      </div>
    </Section>
  )
}

function RowVersion({ label, row }: { label: string; row: Record<string, unknown> }) {
  return (
    <div>
      <div data-type="caption" className="mb-1 text-on-surface-low uppercase tracking-wide">{label}</div>
      <pre data-type="caption" className="max-h-48 overflow-auto rounded-md bg-surface px-3 py-2 text-on-surface-var">{JSON.stringify(row, null, 1)}</pre>
    </div>
  )
}

function DrillLine({ drill }: { drill: DurabilityArchives['last_drill'] }) {
  if (!drill.ran) {
    return (
      <div data-type="caption" className="mb-3 flex items-start gap-2 text-on-surface-low">
        <ShieldQuestion size={13} className="mt-0.5 shrink-0" />
        <span>No restore has been rehearsed yet. Run “Verify a restore” above to check that these snapshots can actually be restored.</span>
      </div>
    )
  }
  const Icon = drill.ok === true ? ShieldCheck : drill.ok === false ? ShieldAlert : ShieldQuestion
  const tone = drill.ok === true ? 'text-success' : drill.ok === false ? 'text-error' : 'text-on-surface-low'
  return (
    <div data-type="caption" className={`mb-3 flex items-start gap-2 ${tone}`}>
      <Icon size={13} className="mt-0.5 shrink-0" />
      <span>
        {drill.ok === true ? 'Last restore drill passed' : drill.ok === false ? 'Last restore drill FAILED' : 'Last restore drill ran; its result was not recorded'}
        {drill.detail && <> — {drill.detail}</>}
      </span>
    </div>
  )
}

function ValidateBadge({ ok, detail }: { ok: boolean | null; detail: string }) {
  const label = ok === true ? 'verified' : ok === false ? 'failed verification' : 'result unknown'
  const tone = ok === true ? 'text-success' : ok === false ? 'text-error' : 'text-on-surface-low'
  return <span data-type="caption" className={`shrink-0 ${tone}`} title={detail || label}>{label}</span>
}

function DomainCounts({ counts }: { counts: DurabilityArchive['domains'] }) {
  if (counts === null) {
    return <div data-type="caption" className="mt-1 text-on-surface-low">Contents not recorded in this snapshot.</div>
  }
  const rows = Object.entries(counts).filter(([, v]) => v.files > 0)
  if (rows.length === 0) {
    return <div data-type="caption" className="mt-1 text-on-surface-low">This snapshot recorded no contents.</div>
  }
  return (
    <ul data-type="caption" className="mt-1 flex list-none flex-wrap gap-x-4 gap-y-0.5 p-0 text-on-surface-low">
      {rows.map(([domain, v]) => (
        <li key={domain}>
          {domain}: {v.rows > 0 ? `${v.rows} rows` : `${v.files} files`}
        </li>
      ))}
    </ul>
  )
}


function usePatch(
  cfg: Record<string, unknown>,
  setCfg: (c: Record<string, unknown>) => void,
  flash: () => void,
) {
  return (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg({ ...cfg, [key]: value })
    api.patchConfig(`durability.${key}`, value).then(flash).catch((e) => {
      setCfg({ ...cfg, [key]: prev })
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
}

function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [saved, () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }]
}

function num(v: unknown, fallback: number): number {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

function NumberRow({ label, hint, value, min, max, suffix, onCommit, saved }: {
  label: string; hint?: string; value: number; min: number; max: number
  suffix?: string
  onCommit: (n: number, label?: string) => void; saved: boolean
}) {
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <NumberField value={value} min={min} max={max} step={1} onChange={(n) => onCommit(n, label)} ariaLabel={label} />
        {suffix && <span data-type="caption" className="w-14 text-on-surface-low">{suffix}</span>}
      </div>
    </Row>
  )
}

function RunButton({ label, icon: Icon, busy, disabled, onClick }: {
  label: string; icon: typeof ShieldCheck; busy: boolean; disabled: boolean; onClick: () => void
}) {
  return (
    <Button variant="secondary" size="xs" onClick={onClick} loading={busy} disabled={disabled} className="gap-1.5"><Icon size={12} className="shrink-0" aria-hidden />
      {label}
    </Button>
  )
}

function JobLine({ label, when, due }: { label: string; when: number; due: boolean }) {
  return (
    <div data-type="body-s" className="flex items-baseline justify-between gap-3">
      <span className="text-on-surface-var">{label}</span>
      <span data-type="caption" className="shrink-0 text-on-surface-low">
        {when ? relativeTime(when) : 'never run'}{due && when ? ' · due' : ''}
      </span>
    </div>
  )
}

function relativeTime(ts: number | string | null | undefined): string {
  const epochSecs = epochSeconds(ts)
  if (epochSecs == null) return ''
  const secs = Math.max(0, Date.now() / 1000 - epochSecs)
  if (secs < 90) return 'just now'
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins} min ago`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`
  const days = Math.round(hours / 24)
  if (days < 31) return `${days} ${days === 1 ? 'day' : 'days'} ago`
  const months = Math.round(days / 30)
  return `${months} ${months === 1 ? 'month' : 'months'} ago`
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = bytes / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`
}
