import { useEffect, useRef, useState } from 'react'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { Download, Trash2, Check, HardDrive, AlertTriangle, X, Lock } from 'lucide-react'
import { api, type AvailableModel } from '../../shared/data/api'
import { SearchField } from '../../shared/ui/SearchField'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { confirmDelete } from '../../shared/ui/dialog'
import { WavyProgress } from '../../shared/ui/WavyProgress'
import { Toggle } from '../../shared/ui/Toggle'
import { Button } from '../../shared/ui/Button'
import { useModelDownloads } from './useModelDownloads'
import { StatusPill } from '../../shared/ui/StatusPill'
import {
  FIT_LABEL, FIT_TONE, budgetKnown, filterByFit, fitDescription, hostFitOf, statedSizeMb, unrunnable,
} from './modelFit'

const MB = (bytes: number) => (bytes / 1024 / 1024).toFixed(0)

const HIDE_LABEL = "Hide models this device can't run"

function FitChip({ model }: { model: AvailableModel }) {
  const verdict = model.fit
  if (!verdict) return null
  const tone = FIT_TONE[verdict]
  const described = fitDescription(model)
  return (
    <StatusPill tone={tone} role="img" aria-label={described} title={described}>
      {FIT_LABEL[verdict]}
    </StatusPill>
  )
}

export function LocalModelManager({
  provider, models, searchable, onChanged,
}: { provider: string; models: AvailableModel[]; searchable?: boolean; onChanged: () => void }) {
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [query, setQuery] = useState('')
  const [searchResults, setSearchResults] = useState<AvailableModel[] | null>(null)
  const [searching, setSearching] = useState(false)
  const searchSeq = useRef(0)

  const { jobs, start, cancel } = useModelDownloads(provider, onChanged)

  const setErr = (name: string, msg: string | null) => setErrors((prev) => {
    const next = { ...prev }; if (msg) next[name] = msg; else delete next[name]; return next
  })
  const download = async (name: string) => {
    setErr(name, null)
    try { await start(name) }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Download failed'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch {   }
      setErr(name, msg)
    }
  }
  const stopDownload = async (name: string) => {
    setErr(name, null)
    try { await cancel(name) }
    catch (e) {
      let msg = e instanceof Error ? e.message : 'Cancel failed'
      try { const p = JSON.parse(msg); msg = p.error || msg } catch {   }
      setErr(name, `Couldn't cancel this download: ${msg}`)
    }
  }
  const remove = async (name: string) => {
    if (!(await confirmDelete('model', name))) return
    setErr(name, null)
    try { await api.deleteLocalModel(provider, name); onChanged() }
    catch (e) { setErr(name, e instanceof Error ? e.message : 'Delete failed') }
  }

  useEffect(() => {
    if (!searchable) return
    const q = query.trim()
    if (!q) { setSearchResults(null); setSearching(false); return }
    const seq = ++searchSeq.current
    setSearching(true)
    const t = setTimeout(async () => {
      try {
        const res = await api.searchLocalModels(provider, q)
        if (seq === searchSeq.current) setSearchResults(res as unknown as AvailableModel[])
      } catch { if (seq === searchSeq.current) setSearchResults([]) }
      finally { if (seq === searchSeq.current) setSearching(false) }
    }, 350)
    return () => clearTimeout(t)
  }, [query, searchable, provider])

  const downloaded = models.filter((m) => m.downloaded).length

  const [showAll, setShowAll] = useState<boolean | null>(null)
  const hostFit = hostFitOf(models)
  const fitFilterable = budgetKnown(hostFit)
  const hiding = showAll === null ? fitFilterable && !!hostFit?.hide_unrunnable : !showAll
  const unrunnableCount = unrunnable(models, hostFit).length
  const showSearch = searchable && query.trim().length > 0
  const rows: AvailableModel[] = showSearch
    ? (searchResults ?? [])
    : filterByFit(models, hostFit, hiding)
  const hiddenCount = showSearch ? 0 : models.length - rows.length

  const renderRow = (m: AvailableModel) => {
    const job = jobs[m.name]
    const downloading = job?.state === 'running'
    const err = errors[m.name] || (job?.state === 'error' ? job.error : '')
    const frac = job && job.total_bytes > 0 ? job.progress : undefined
    const sizeMb = m.size_mb ?? (m.size ? Math.round(m.size / 1024 / 1024) : 0)
    const stated = statedSizeMb(m)
    // about this machine, not a licence to block a download the user may want anyway.
    const stepDown = m.fit === 'red' ? m.fit_step_down : null
    const gatedUndownloaded = m.gated && !m.downloaded
    return (
      <div key={m.name} className="rounded-md px-2.5 py-1.5"
        style={m.downloaded
          ? { background: 'color-mix(in srgb, var(--color-primary) 8%, transparent)', boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--color-primary) 18%, transparent)' }
          : { background: 'var(--color-surface-high)' }}>
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <span data-type="caption" className="truncate text-on-surface font-mono">{m.name}</span>
              {m.downloaded && <Check size={11} style={{ color: 'var(--color-success)' }} />}
              {gatedUndownloaded && <Lock size={10} className="shrink-0 text-on-surface-low" aria-label="Requires a token / license" />}
              <FitChip model={m} />
            </div>
            <div data-type="caption" className="truncate text-on-surface-low">
              {downloading
                ? `downloading${job.downloaded_bytes ? ` · ${MB(job.downloaded_bytes)}${sizeMb ? ` / ${sizeMb}` : ''} MB` : ''}`
                : <>{m.description || (m.capabilities?.length ? m.capabilities.join(', ') : '')}{stated.mb ? ` · ${stated.mb} MB` : ''}{stated.familyMedianMb ? ` · family median ~${stated.familyMedianMb} MB` : ''}</>}
            </div>
            {stepDown && !downloading && (
              <Button variant="ghost-accent" size="xs" className="-ml-m mt-0.5"
                onClick={() => download(stepDown)}>
                Download {stepDown} instead — it fits
              </Button>
            )}
            {
}
            {downloading && (
              <div className="mt-1">
                {frac == null
                  ? <WavyProgress width={200} />
                  : <WavyProgress width={200} value={frac} label={`Downloading ${m.name}`} />}
              </div>
            )}
          </div>
          {downloading ? (
            <SquareIconButton icon={X} iconSize={13} label={`Cancel ${m.name}`} title="Cancel"
              onClick={() => stopDownload(m.name)} className="shrink-0" />
          ) : (
            <SquareIconButton icon={m.downloaded ? Trash2 : Download} iconSize={13}
              label={m.downloaded ? `Delete ${m.name}` : `Download ${m.name}`}
              title={gatedUndownloaded ? 'Requires a token / license (see provider settings)' : m.downloaded ? 'Delete' : 'Download'}
              disabled={gatedUndownloaded}
              onClick={() => (m.downloaded ? remove(m.name) : download(m.name))} className="shrink-0" />
          )}
        </div>
        {err && (
          <div data-type="caption" className="mt-1 flex items-start gap-1" style={{ color: 'var(--color-danger)' }}>
            <AlertTriangle size={11} className="mt-0.5 shrink-0" /> <span className="min-w-0">{err}</span>
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div data-type="caption" className="mb-1.5 flex items-center gap-1 text-on-surface-low uppercase tracking-wide">
        <HardDrive size={11} /> Models ({downloaded}/{models.length} downloaded)
      </div>

      {
}
      {fitFilterable && (
        <div data-type="caption" className="mb-1.5 flex items-center gap-1.5">
          <Toggle size="sm" on={hiding} onChange={(v) => setShowAll(!v)} label={HIDE_LABEL} />
          <span className="text-on-surface-low">{HIDE_LABEL}</span>
          {hiding && hiddenCount > 0 && (
            <span style={{ color: 'var(--color-warning)' }}>
              {hiddenCount} hidden
            </span>
          )}
          {!hiding && unrunnableCount > 0 && (
            <span className="text-on-surface-low">{`${unrunnableCount} won't fit`}</span>
          )}
        </div>
      )}

      {searchable && (
        <div className="mb-1.5">
          <SearchField value={query} onChange={setQuery} size="sm"
            placeholder="Search the library to install a model…"
            ariaLabel="Search the model library" />
          {
}
          <ResultAnnouncement count={searchResults?.length ?? 0} noun="models"
            active={!!query.trim() && !searching && searchResults !== null} />
        </div>
      )}

      {showSearch && searching && rows.length === 0 ? (
        <div data-type="caption" className="py-1 text-on-surface-low italic">Searching…</div>
      ) : rows.length === 0 ? (
        <div data-type="caption" className="py-1 text-on-surface-low italic">
          {
}
          {showSearch ? `No models match “${query.trim()}”.`
            : hiddenCount === 1
              ? 'The only listed model is hidden — it will not run on this device.'
              : hiddenCount > 1
                ? `All ${hiddenCount} listed models are hidden — none of them will run on this device.`
                : 'No downloadable models listed.'}
        </div>
      ) : (
        <div className="grid gap-1.5 [grid-template-columns:repeat(auto-fill,minmax(260px,1fr))]">
          {rows.map(renderRow)}
        </div>
      )}
    </div>
  )
}
