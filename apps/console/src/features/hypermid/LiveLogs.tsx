import { useEffect, useRef, useState } from 'react'
import { Pause, Play, RefreshCw } from 'lucide-react'
import { api, type HypermidCursorWire } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { EmptyState, FormSkeleton } from '../../shared/ui/ListScaffold'
import { TextInput, Select } from '../../shared/ui/forms'
import { Surface } from '../../shared/ui/Surface'
import { Section } from '../settings/settingsUI'
import { mergeLogPage, type HypermidLogBuffer } from './diagnosticsState'

const EMPTY: HypermidLogBuffer = { entries: [], gap: false, recoveryCursor: null }

export function LiveLogs() {
  const [buffer, setBuffer] = useState<HypermidLogBuffer>(EMPTY)
  const [visible, setVisible] = useState(EMPTY.entries)
  const [severity, setSeverity] = useState('')
  const [component, setComponent] = useState('')
  const [paused, setPaused] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const cursor = useRef<HypermidCursorWire | undefined>(undefined)
  const filters = useRef({ severity, component })
  filters.current = { severity, component }

  const load = async (resume: boolean) => {
    try {
      const page = await api.hypermidLogs({ after: resume ? cursor.current : undefined, ...filters.current, limit: 200 })
      setBuffer((current) => mergeLogPage(resume ? current : EMPTY, page))
      cursor.current = page.cursor
      setError('')
    } catch (caught) { setError(caught instanceof Error ? caught.message : 'Hypermid logs are unavailable.') }
    finally { setLoading(false) }
  }

  useEffect(() => {
    cursor.current = undefined
    setBuffer(EMPTY); setLoading(true)
    void load(false)
    const timer = window.setInterval(() => void load(true), 5_000)
    return () => window.clearInterval(timer)
  // load intentionally reads current filter values from refs without restarting for log arrival.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [severity, component])
  useEffect(() => { if (!paused) setVisible(buffer.entries) }, [buffer.entries, paused])

  const togglePause = () => {
    if (!paused) setVisible(buffer.entries)
    setPaused((value) => !value)
  }
  return <Section title="Live logs" hint="Bounded and redacted before display. Pause freezes this view while collection continues.">
    <div className="mb-m grid gap-s sm:grid-cols-[12rem_minmax(0,1fr)_auto]">
      <Select value={severity} onChange={(value) => setSeverity(value)} ariaLabel="Log severity" className="min-h-11 min-w-0 rounded-lg border border-outline-variant bg-surface px-m text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" options={[{ value: "", label: "All severities" }, { value: "error", label: "Errors" }, { value: "warning", label: "Warnings" }, { value: "info", label: "Information" }, { value: "debug", label: "Debug" }]} />
      <TextInput value={component} onChange={setComponent} ariaLabel="Log component" placeholder="Filter by component" size="sm" />
      <div className="flex flex-wrap gap-s"><Button size="sm" variant="secondary" onClick={togglePause}>{paused ? <Play size={14} /> : <Pause size={14} />}{paused ? 'Resume view' : 'Pause view'}</Button>
        <Button size="sm" variant="secondary" onClick={() => void load(true)}><RefreshCw size={14} /> Refresh</Button></div>
    </div>
    {buffer.gap && <p data-type="body-s" role="alert" className="mb-m rounded-lg bg-warn/10 px-m py-s text-on-surface">Older log entries are no longer retained. Display resumed from the daemon recovery cursor.</p>}
    {error && <p data-type="body-s" role="alert" className="mb-m break-words text-danger">{error}</p>}
    {loading && !buffer.cursor ? <FormSkeleton sections={1} rows={3} what="Hypermid logs" /> : visible.length === 0
      ? <EmptyState title="No log entries" hint="No retained entries match these filters." />
      : <div className="grid gap-s" role="group" aria-label="Hypermid log entries">{visible.map((entry) => <Surface key={`${entry.cursor.epoch}:${entry.cursor.sequence}`} tone="container" radius="lg" className="min-w-0 p-m">
        <div className="flex flex-wrap items-center gap-s text-on-surface-low"><span className="uppercase" data-type="label-s">{entry.severity}</span><span data-type="caption">{entry.component}</span><span data-type="caption">{new Date(entry.observed_at).toLocaleString()}</span></div>
        <p data-type="body-m" className="mt-xs break-words text-on-surface">{entry.message}</p>
      </Surface>)}</div>}
    <p role="status" aria-live="polite" className="sr-only">{paused ? 'Log view paused.' : `Showing ${visible.length} log entries.`}</p>
  </Section>
}
