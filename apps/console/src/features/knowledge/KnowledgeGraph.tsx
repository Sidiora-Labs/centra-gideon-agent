import { useEffect, useMemo, useRef, useState } from 'react'
import { Loader2, Network, Sparkles } from 'lucide-react'
import { GraphZoomControls } from '../../shared/ui/GraphZoomControls'
import { EmptyState } from '../../shared/ui/ListScaffold'

interface GraphNode {
  id: string
  name?: string
  type?: string
  x?: number | null
  y?: number | null
  degree?: number | null
  cluster?: number | string | null
}
interface GraphEdge {
  source: string
  target: string
  type?: string
  weight?: number | null
}

const W = 1000, H = 1000, cx = W / 2, cy = H / 2
const PAD = 60

const finite = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

export function hasProjection(nodes: GraphNode[]): boolean {
  return nodes.some((n) => {
    const x = finite(n.x), y = finite(n.y)
    return x !== null && y !== null && (x !== 0 || y !== 0)
  })
}

export function projectPositions(nodes: GraphNode[]): Map<string, { x: number; y: number }> {
  const unit = (v: number | null) => (v === null ? 0.5 : Math.min(1, Math.max(0, (v + 1) / 2)))
  const m = new Map<string, { x: number; y: number }>()
  for (const n of nodes) {
    m.set(n.id, {
      x: PAD + unit(finite(n.x)) * (W - 2 * PAD),
      y: PAD + unit(finite(n.y)) * (H - 2 * PAD),
    })
  }
  return m
}

export function ringPositions(nodes: GraphNode[]): Map<string, { x: number; y: number }> {
  const m = new Map<string, { x: number; y: number }>()
  const n = nodes.length
  nodes.forEach((node, i) => {
    const ring = i < 1 ? 0 : i < 9 ? 1 : i < 25 ? 2 : 3
    const r = ring === 0 ? 0 : ring === 1 ? 160 : ring === 2 ? 300 : 440
    const ringStart = ring === 0 ? 0 : ring === 1 ? 1 : ring === 2 ? 9 : 25
    const ringCount = ring === 0 ? 1 : ring === 1 ? Math.min(8, n - 1) : ring === 2 ? Math.min(16, n - 9) : n - 25
    const idx = i - ringStart
    const a = (idx / Math.max(1, ringCount)) * Math.PI * 2 - Math.PI / 2
    m.set(node.id, { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r })
  })
  return m
}

export function nodeRadius(degree: number): number {
  return Math.min(16, 6 + degree * 1.5)
}

const LABEL_FONT = 10
const LABEL_ADVANCE = 0.55
const LABEL_LINE = 1.2
const LABEL_GAP = 4
const LABEL_MIN_PX = 7

interface LabelRect { x1: number; x2: number; y1: number; y2: number }
interface LabelCandidate { id: string; name: string; r: number; x: number; y: number }

export function labelRect(name: string, r: number, x: number, y: number): LabelRect {
  const w = Math.max(1, name.length) * LABEL_FONT * LABEL_ADVANCE
  const h = LABEL_FONT * LABEL_LINE
  const bottom = y - r - LABEL_GAP
  return { x1: x - w / 2, x2: x + w / 2, y1: bottom - h, y2: bottom }
}

export function placeLabels(candidates: LabelCandidate[], renderedPx: number): Set<string> {
  const placed = new Set<string>()
  if (renderedPx < LABEL_MIN_PX) return placed
  const taken: LabelRect[] = []
  for (const c of [...candidates].sort((a, b) => b.r - a.r)) {
    const box = labelRect(c.name, c.r, c.x, c.y)
    if (taken.some((t) => box.x1 < t.x2 && box.x2 > t.x1 && box.y1 < t.y2 && box.y2 > t.y1)) continue
    taken.push(box)
    placed.add(c.id)
  }
  return placed
}

export function weightSpan(edges: GraphEdge[]): { lo: number; hi: number } {
  let lo = Infinity, hi = -Infinity
  for (const e of edges) {
    const w = finite(e.weight)
    if (w === null) continue
    lo = Math.min(lo, w)
    hi = Math.max(hi, w)
  }
  return hi > lo ? { lo, hi } : { lo: 0, hi: 0 }
}

export function weightFraction(weight: number | null | undefined, span: { lo: number; hi: number }): number {
  const w = finite(weight)
  if (w === null || span.hi <= span.lo) return 0
  return Math.min(1, Math.max(0, (w - span.lo) / (span.hi - span.lo)))
}

export function weightStroke(f: number): string {
  return `color-mix(in srgb, var(--color-on-surface) ${Math.round(f * 70)}%, var(--color-on-surface-low))`
}

export function weightWidth(f: number, active: boolean): number {
  return Math.round(((active ? 1.6 : 1) + f * 1.4) * 100) / 100
}

export function KnowledgeGraph({ selectedId, onSelect, onRegenerate, regenerating }: {
  selectedId?: string | null
  onSelect?: (name: string) => void
  onRegenerate?: () => void
  regenerating?: boolean
} = {}) {
  const [graph, setGraph] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const [view, setView] = useState({ scale: 1, x: 0, y: 0 })
  const [pxPerWorld, setPxPerWorld] = useState(1)
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null)
  const svgRef = useRef<SVGSVGElement | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/knowledge/graph', { headers: { 'X-Session-Key': 'dashboard:ui' } })
      .then((r) => r.json()).then((d) => { if (alive) setGraph(d) }).catch(() => { if (alive) setGraph({ nodes: [], edges: [] }) })
    return () => { alive = false }
  }, [])

  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const measure = () => { if (el.clientWidth > 0) setPxPerWorld(el.clientWidth / W) }
    measure()
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    ro?.observe(el)
    window.addEventListener('resize', measure)
    return () => { ro?.disconnect(); window.removeEventListener('resize', measure) }
  }, [graph])

  const pos = useMemo(() => {
    const nodes = graph?.nodes ?? []
    return hasProjection(nodes) ? projectPositions(nodes) : ringPositions(nodes)
  }, [graph])

  const degree = useMemo(() => {
    const d = new Map<string, number>()
    for (const e of graph?.edges ?? []) {
      d.set(e.source, (d.get(e.source) ?? 0) + 1)
      d.set(e.target, (d.get(e.target) ?? 0) + 1)
    }
    for (const n of graph?.nodes ?? []) {
      const declared = finite(n.degree)
      if (declared !== null) d.set(n.id, declared)
    }
    return d
  }, [graph])

  const span = useMemo(() => weightSpan(graph?.edges ?? []), [graph])

  const labelled = useMemo(
    () =>
      placeLabels(
        (graph?.nodes ?? []).flatMap((n) => {
          const p = pos.get(n.id)
          return p && n.name ? [{ id: n.id, name: n.name, r: nodeRadius(degree.get(n.id) ?? 0), x: p.x, y: p.y }] : []
        }),
        LABEL_FONT * view.scale * pxPerWorld,
      ),
    [graph, pos, degree, view.scale, pxPerWorld],
  )

  const zoomBy = (factor: number) => setView((v) => ({ ...v, scale: Math.min(6, Math.max(0.3, v.scale * factor)) }))
  const reset = () => setView({ scale: 1, x: 0, y: 0 })

  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12
      setView((v) => ({ ...v, scale: Math.min(6, Math.max(0.3, v.scale * factor)) }))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [graph])

  const onPointerDown = (e: React.PointerEvent) => {
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y }
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return
    const k = (W / (svgRef.current?.clientWidth || W)) / view.scale
    setView((v) => ({ ...v, x: drag.current!.vx + (e.clientX - drag.current!.x) * k, y: drag.current!.vy + (e.clientY - drag.current!.y) * k }))
  }
  const endDrag = () => { drag.current = null }

  if (!graph) return <div className="grid h-full place-items-center text-on-surface-low"><Loader2 size={20} className="animate-spin" /></div>
  if (graph.nodes.length === 0) {
    return (
      <div className="grid h-full place-items-center">
        <EmptyState icon={Network} title="No entities extracted yet"
          hint="Your items have not been through entity extraction, so there is nothing to draw. Running it re-derives insights for items that are missing them."
          action={onRegenerate ? { label: regenerating ? 'Extracting…' : 'Regenerate intelligence', onClick: regenerating ? () => {} : onRegenerate, icon: Sparkles } : undefined} />
      </div>
    )
  }

  const transform = `translate(${cx} ${cy}) scale(${view.scale}) translate(${-cx + view.x} ${-cy + view.y})`

  return (
    <div className="relative h-full w-full overflow-hidden">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        className="h-full w-full touch-none select-none"
        style={{ cursor: drag.current ? 'grabbing' : 'grab' }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={endDrag} onPointerLeave={endDrag}
      >
        <g transform={transform} style={{ transition: drag.current ? 'none' : 'transform 200ms ease-out' }}>
        {graph.edges.map((e, i) => {
          const a = pos.get(e.source), b = pos.get(e.target)
          if (!a || !b) return null
          const active = hover === e.source || hover === e.target
          const f = weightFraction(e.weight, span)
          return <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} vectorEffect="non-scaling-stroke" stroke={active ? 'var(--color-primary)' : weightStroke(f)} strokeWidth={weightWidth(f, active)} strokeOpacity={hover && !active ? 0.15 : 0.7} />
        })}
        {graph.nodes.map((n) => {
          const p = pos.get(n.id); if (!p) return null
          const r = nodeRadius(degree.get(n.id) ?? 0)
          const selected = selectedId != null && (n.name === selectedId || n.id === selectedId)
          const active = hover === n.id || selected
          return (
            <g key={n.id} data-entity-id={n.id} transform={`translate(${p.x},${p.y})`}
              onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(n.name ?? n.id)}
              style={{ cursor: 'pointer' }} opacity={hover && !active ? 0.4 : 1}>
              {
}
              <circle r={r} vectorEffect="non-scaling-stroke" fill={selected ? 'var(--color-primary)' : 'color-mix(in srgb, var(--color-primary) 30%, var(--color-surface))'} stroke={active ? 'var(--color-primary)' : 'var(--color-on-surface-low)'} strokeWidth={active ? 2.5 : 1} />
              {
}
              {n.name && (active || labelled.has(n.id)) && <text y={-r - LABEL_GAP} textAnchor="middle" className="fill-on-surface" style={{ fontSize: LABEL_FONT }}>{n.name}</text>}
            </g>
          )
        })}
        </g>
      </svg>

      { }
      <GraphZoomControls onZoomIn={() => zoomBy(1.25)} onZoomOut={() => zoomBy(1 / 1.25)} onReset={reset} />
      <div data-type="caption" className="absolute bottom-3 left-3 rounded-pill bg-surface-high/80 px-2 py-0.5 text-on-surface-low tabular-nums backdrop-blur">{Math.round(view.scale * 100)}%</div>
    </div>
  )
}
