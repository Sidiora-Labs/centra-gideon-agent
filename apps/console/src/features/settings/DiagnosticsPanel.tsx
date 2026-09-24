import { LoadError } from '../../shared/ui/ListScaffold'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { Pause, Play, Trash2, ArrowDownToLine } from 'lucide-react'
import { Surface } from '../../shared/ui/Surface'
import { SearchField } from '../../shared/ui/SearchField'
import { WindowedList } from '../../shared/ui/WindowedList'
import { withWeight } from '../../shared/theme/fontWeight'
import { api } from '../../shared/data/api'
import { accentChip } from '../../shared/theme/accent'
import { PanelHeader, Section } from './settingsUI'
import { reportActionFailure } from '../../app/shell/reportingWrite'

interface LogEntry { level: string; msg: string; key: number }

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR'] as const
type Level = (typeof LEVELS)[number]

const RANK: Record<string, number> = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40 }

const LEVEL_TONE: Record<string, string> = {
  DEBUG: 'var(--color-on-surface-low)',
  INFO: 'var(--color-info)',
  WARNING: 'var(--color-warn)',
  ERROR: 'var(--color-danger)',
}

const MAX_ENTRIES = 2000

export function DiagnosticsPanel() {
  const [levelErr, setLevelErr] = useState<unknown>(null)
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [paused, setPaused] = useState(false)
  const [filter, setFilter] = useState('')
  const [minLevel, setMinLevel] = useState<Level>('DEBUG')
  const [level, setLevel] = useState<string>('')
  const [levelBusy, setLevelBusy] = useState(false)
  const [connected, setConnected] = useState(false)
  const [autoscroll, setAutoscroll] = useState(true)

  const pausedRef = useRef(paused)
  pausedRef.current = paused
  const keyRef = useRef(0)
  const scrollRef = useRef<HTMLDivElement>(null)

  const loadLevel = () => { setLevelErr(null); api.logLevel().then(setLevel).catch(setLevelErr) }
  useEffect(loadLevel, [])

  useEffect(() => {
    let es: EventSource | null = null
    try { es = new EventSource(api.logsUrl(300)) } catch { return }
    es.onopen = () => setConnected(true)
    es.onmessage = (e) => {
      if (pausedRef.current) return
      try {
        const d = JSON.parse(e.data) as { level?: string; msg?: string }
        if (typeof d.msg !== 'string') return
        const entry: LogEntry = { level: (d.level || 'INFO').toUpperCase(), msg: d.msg, key: keyRef.current++ }
        setEntries((prev) => {
          const next = prev.length >= MAX_ENTRIES ? prev.slice(prev.length - MAX_ENTRIES + 1) : prev
          return [...next, entry]
        })
      } catch {   }
    }
    es.onerror = () => setConnected(false)
    return () => { es?.close() }
  }, [])

  useEffect(() => {
    if (autoscroll && !paused && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries, autoscroll, paused])

  const q = filter.trim().toLowerCase()
  const visible = useMemo(
    () => entries.filter((e) => RANK[e.level] >= RANK[minLevel] && (q === '' || e.msg.toLowerCase().includes(q))),
    [entries, minLevel, q],
  )

  const changeLevel = async (l: Level) => {
    if (levelBusy) return
    setLevelBusy(true)
    await api.setLogLevel(l)
      .then((r) => setLevel(r.level))
      .catch(reportActionFailure(`set the log level to ${l}`))
    setLevelBusy(false)
  }

  return (
    <div>
      {
}
      <PanelHeader
        title="Diagnostics"
        hint="A live tail of the gateway's own log, plus the backend log level. Doctor probing is read-only; its Fix and Run now controls are the only exceptions that mutate. Changing the log level here also writes that setting, which persists across restarts."
      />

      { }
      {
}
      <Section title="Backend log level" hint="Change how verbose the gateway logs are, live. Persists across restarts.">
        {levelErr && <LoadError what="backend log level" error={levelErr} onRetry={loadLevel} />}
        <Surface tone="container" radius="lg" className="px-l py-m">
          <div className="flex items-center gap-s">
            <div className="inline-flex rounded-pill bg-surface-container p-1">
              {
}
              {LEVELS.map((l) => {
                const on = level === l
                return (
                  <button key={l} aria-label={`Backend log level: ${l}`} aria-pressed={on}
                    onClick={() => changeLevel(l)} disabled={levelBusy || !!levelErr || !level}
                    data-type="body-s" className="rounded-pill px-m h-8 transition-colors disabled:opacity-60"
                    style={on ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                    {l}
                  </button>
                )
              })}
            </div>
            { }
            {level && <span className="text-on-surface-low text-[0.75rem]">Current: <strong className="text-on-surface-var">{level}</strong></span>}
          </div>
        </Surface>
      </Section>

      { }
      <Section
        title="Live logs"
        hint={
          <span className="flex items-center gap-1.5">
            <span className="inline-block size-1.5 rounded-pill" style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
            {connected ? 'Streaming' : 'Connecting…'} · {visible.length} shown{entries.length !== visible.length ? ` of ${entries.length}` : ''}
          </span>
        }
        right={
          <div className="flex items-center gap-s">
            {
}
            <span data-type="caption" className="hidden shrink-0 text-on-surface-low sm:inline">Show</span>
            { }
            <div className="inline-flex rounded-pill bg-surface-container p-1">
              {LEVELS.map((l) => {
                const on = minLevel === l
                return (
                  <button key={l} aria-label={`Show ${l} and above`} aria-pressed={on}
                    onClick={() => setMinLevel(l)} title={`Show ${l} and above`}
                    data-type="caption" className="rounded-pill px-2.5 h-7 transition-colors"
                    style={on ? { background: 'var(--color-surface-highest)', color: 'var(--color-on-surface)' } : { color: 'var(--color-on-surface-low)' }}>
                    {l}
                  </button>
                )
              })}
            </div>
            <button onClick={() => setAutoscroll((v) => !v)} aria-pressed={autoscroll} title={autoscroll ? 'Autoscroll on' : 'Autoscroll off'}
              className="inline-flex items-center justify-center size-8 rounded-lg transition-colors"
              style={autoscroll ? accentChip : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}>
              <ArrowDownToLine size={15} />
            </button>
            <button onClick={() => setPaused((v) => !v)} title={paused ? 'Resume' : 'Pause'}
              className="inline-flex items-center justify-center size-8 rounded-lg bg-surface-high text-on-surface-var transition-colors hover:bg-surface-highest">
              {paused ? <Play size={15} /> : <Pause size={15} />}
            </button>
            <button onClick={() => setEntries([])} title="Clear"
              className="inline-flex items-center justify-center size-8 rounded-lg bg-surface-high text-on-surface-var transition-colors hover:text-danger">
              <Trash2 size={15} />
            </button>
          </div>
        }
      >

        { }
        <div className="mb-s">
          <SearchField value={filter} onChange={setFilter} size="md" placeholder="Filter log lines…"
            ariaLabel="Filter log lines" />
          {
}
          <ResultAnnouncement count={visible.length} noun="lines"
            active={q !== '' || minLevel !== 'DEBUG'} />
        </div>

        <Surface tone="container" radius="lg" className="p-0 overflow-hidden">
          {
}
          {
}
          <div ref={scrollRef} data-type="caption" className="max-h-[60vh] min-h-[240px] overflow-y-auto p-3 font-mono leading-relaxed focus-visible:-outline-offset-2"
            tabIndex={0} role="group" aria-label="Log output"
            style={{ fontFamily: '"JetBrains Mono", ui-monospace, monospace' }}>
            {visible.length === 0 ? (
              <div data-type="body-s" className="py-8 text-center text-on-surface-low" style={{ fontFamily: 'var(--font-sans)' }}>
                {paused ? 'Paused — resume to see live logs.' : entries.length === 0 ? 'Waiting for log entries…' : 'No lines match the current filter.'}
              </div>
            ) : (
              // region is deliberately ONE tab stop (the `tabIndex/role=group/aria-label`
              <WindowedList
                items={visible}
                rowKey={(e) => String(e.key)}
                rowHeights="variable"
                estimateRowHeight={24}
                gap={0}
                noun="lines"
                findHint="use the Filter log lines field above, which filters every buffered line."
                enableRowKeyboard={false}
              >
                {(e) => (
                  <div className="whitespace-pre-wrap break-words border-b border-outline-variant/20 py-0.5">
                    <span style={withWeight({ color: LEVEL_TONE[e.level] ?? 'var(--color-on-surface-low)' }, 600)}>{e.level.padEnd(7)}</span>
                    <span className="text-on-surface-var"> {e.msg}</span>
                  </div>
                )}
              </WindowedList>
            )}
          </div>
        </Surface>
      </Section>
    </div>
  )
}
