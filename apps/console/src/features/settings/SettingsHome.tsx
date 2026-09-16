import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Eyebrow } from '../../shared/ui/Eyebrow'
import { ResultAnnouncement } from '../../shared/ui/ListControls'
import { SearchX } from 'lucide-react'
import { SearchField } from '../../shared/ui/SearchField'
import { SETTINGS_WIDGETS, type SettingsWidget } from './settingsWidgets'

const COL_MIN = 320
const COL_GAP = 12

export function SettingsHome({ go }: { go: (id: string) => void }) {
  const [query, setQuery] = useState('')
  const q = query.trim()

  const groups = useMemo(() => {
    const order: string[] = []
    const byGroup: Record<string, SettingsWidget[]> = {}
    for (const w of SETTINGS_WIDGETS) {
      if (!byGroup[w.group]) { byGroup[w.group] = []; order.push(w.group) }
      byGroup[w.group].push(w)
    }
    return order.map((g) => ({ title: g, items: byGroup[g] }))
  }, [])

  const [matches, setMatches] = useState<Record<string, boolean>>({})
  const anyMatch = q === '' || Object.values(matches).some(Boolean)
  const reportMatch = useCallback((id: string, m: boolean) => {
    setMatches((prev) => (prev[id] === m ? prev : { ...prev, [id]: m }))
  }, [])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto w-full px-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {
}
          <div className="sticky top-0 z-20 -mx-2xl bg-canvas px-2xl pt-2xl pb-l">
            {
}
            <div data-tour="settings" className="mx-auto w-full" style={{ maxWidth: 720 }}>
              <SearchField value={query} onChange={setQuery} autoFocus
                placeholder="Search settings — name, description, or any value inside"
                ariaLabel="Search settings" />
              {
}
              <ResultAnnouncement count={Object.values(matches).filter(Boolean).length} noun="settings"
                active={q !== ''} />
            </div>
          </div>

          {!anyMatch && (
            <div className="flex flex-col items-center gap-2 py-2xl text-center text-on-surface-low">
              <SearchX size={28} className="opacity-50" />
              <p data-type="body-m">No settings match “{q}”.</p>
            </div>
          )}

          <div className="pb-2xl" style={anyMatch ? undefined : { display: 'none' }}>
            <BalancedColumns
              items={groups.map((g) => ({
                key: g.title,
                weight: g.items.length,
                node: <Group title={g.title} items={g.items} query={q} go={go} onMatch={reportMatch} />,
              }))}
            />
          </div>
        </div>
      </div>
    </div>
  )
}

function BalancedColumns({ items }: { items: { key: string; weight: number; node: React.ReactNode }[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const blockRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const heights = useRef<Map<string, number>>(new Map())
  const [cols, setCols] = useState(1)
  const [layout, setLayout] = useState<string[][]>([])

  useLayoutEffect(() => {
    const el = containerRef.current
    if (!el) return
    const apply = () => {
      const w = el.clientWidth
      const n = Math.max(1, Math.min(items.length, Math.floor((w + COL_GAP) / (COL_MIN + COL_GAP))))
      setCols((prev) => (prev === n ? prev : n))
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [items.length])

  const order = useMemo(
    () => items.map((it) => it.key).sort((a, b) => {
      const wa = items.find((x) => x.key === a)!.weight
      const wb = items.find((x) => x.key === b)!.weight
      return wb - wa
    }),
    [items],
  )

  useLayoutEffect(() => {
    for (const [k, el] of blockRefs.current) heights.current.set(k, el.offsetHeight)
    const colKeys: string[][] = Array.from({ length: cols }, () => [])
    const colH = new Array(cols).fill(0)
    for (const key of order) {
      let m = 0
      for (let i = 1; i < cols; i++) if (colH[i] < colH[m]) m = i
      colKeys[m].push(key)
      colH[m] += (heights.current.get(key) ?? 1) + COL_GAP
    }
    const same = layout.length === colKeys.length && colKeys.every((c, i) => {
      const p = layout[i]; return p && p.length === c.length && c.every((k, j) => p[j] === k)
    })
    if (!same) setLayout(colKeys)
  })

  const byKey = (k: string) => items.find((it) => it.key === k)!
  const columns = layout.length === cols && cols > 0 ? layout : [order]

  return (
    <div ref={containerRef} className="flex items-start" style={{ gap: COL_GAP }}>
      {columns.map((keys, ci) => (
        <div key={ci} className="flex min-w-0 flex-1 flex-col" style={{ gap: COL_GAP }}>
          {keys.map((k) => (
            <div key={k} ref={(el) => { if (el) blockRefs.current.set(k, el); else blockRefs.current.delete(k) }}>
              {byKey(k).node}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

function Group({ title, items, query, go, onMatch }: {
  title: string; items: SettingsWidget[]; query: string; go: (id: string) => void
  onMatch: (id: string, matched: boolean) => void
}) {
  const [visible, setVisible] = useState<Record<string, boolean>>({})
  const anyVisible = query === '' || Object.values(visible).some(Boolean)
  const report = useCallback((id: string, m: boolean) => {
    setVisible((p) => (p[id] === m ? p : { ...p, [id]: m }))
    onMatch(id, m)
  }, [onMatch])
  return (
    <section style={anyVisible ? undefined : { display: 'none' }}>
      <Eyebrow as="h2" className="mb-2 px-1">{title}</Eyebrow>
      <div className="flex flex-col" style={{ gap: COL_GAP }}>
        {items.map((w) => <Cell key={w.id} widget={w} query={query} go={go} onMatch={report} />)}
      </div>
    </section>
  )
}

function Cell({ widget, query, go, onMatch }: {
  widget: SettingsWidget; query: string; go: (id: string) => void; onMatch: (id: string, matched: boolean) => void
}) {
  const text = widget.useSearchText()
  const haystack = `${widget.label} ${widget.description} ${text}`.toLowerCase()
  const matched = query === '' || haystack.includes(query.toLowerCase())
  const reportRef = useRef(onMatch); reportRef.current = onMatch
  useEffect(() => { reportRef.current(widget.id, matched) }, [matched, widget.id])
  return <div style={matched ? undefined : { display: 'none' }}>{widget.render(query, go)}</div>
}
