import { useMemo, useState } from 'react'
import { MemoryGraph } from '../settings/MemoryGraph'
import { litNeighbourhood } from '../../shared/data/litSet'
import { Segmented } from '../../shared/ui/Segmented'
import { Select } from '../../shared/ui/forms'
import { TextLink } from '../../shared/ui/TextLink'
import type { MemoryGraphData } from '../../shared/data/api'

export interface KnowledgeGraphNode {
  id: string
  name?: string
  type?: string
}
export interface KnowledgeGraphEdge {
  source: string
  target: string
  type?: string
  weight?: number
}
export interface KnowledgeGraphPayload {
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
}

const HOP_DEPTHS = [1, 2, 3]

export function KnowledgeEgoGraph({
  data,
  focusId,
  boxHeight,
  onSelect,
  onShowAll,
  defaultHopDepth = 1,
}: {
  data: KnowledgeGraphPayload
  focusId: string
  boxHeight?: number
  onSelect?: (id: string) => void
  onShowAll?: () => void
  defaultHopDepth?: number
}) {
  const [hopDepth, setHopDepth] = useState(defaultHopDepth)
  const [linkType, setLinkType] = useState('')

  const linkTypeOptions = useMemo(() => {
    const seen = new Set<string>()
    for (const e of data.edges) if (e.type) seen.add(e.type)
    return [
      { value: '', label: 'Any link type' },
      ...[...seen].sort().map((t) => ({ value: t, label: t.replace(/_/g, ' ') })),
    ]
  }, [data])

  const edges = useMemo(
    () =>
      data.edges
        .filter((e) => !linkType || e.type === linkType)
        .map((e) => ({ from: e.source, to: e.target, type: e.type })),
    [data, linkType],
  )

  const lit = useMemo(() => litNeighbourhood(edges, focusId, hopDepth), [edges, focusId, hopDepth])

  const canvas: MemoryGraphData = useMemo(() => {
    const keep = lit ?? new Set<string>()
    return {
      nodes: data.nodes
        .filter((n) => keep.has(n.id))
        .map((n) => ({
          id: n.id,
          label: n.name || n.id,
          group: n.type || undefined,
          title: n.name || n.id,
          ref: n.id,
        })),
      edges: edges.filter((e) => keep.has(e.from) && keep.has(e.to)),
    }
  }, [data, edges, lit])

  const focusNode = useMemo(() => data.nodes.find((n) => n.id === focusId) ?? null, [data, focusId])
  const hasAnyLink = useMemo(
    () => data.edges.some((e) => e.source === focusId || e.target === focusId),
    [data, focusId],
  )

  const neighbours = useMemo(() => {
    const keep = lit ?? new Set<string>()
    const types = new Map<string, Set<string>>()
    for (const e of edges) {
      if (!keep.has(e.from) || !keep.has(e.to)) continue
      for (const side of [e.from, e.to]) {
        if (side === focusId) continue
        const bucket = types.get(side) ?? types.set(side, new Set<string>()).get(side)!
        bucket.add(e.type || 'link')
      }
    }
    return canvas.nodes
      .filter((n) => n.id !== focusId)
      .map((n) => ({ id: n.id, label: n.label, types: [...(types.get(n.id) ?? ['link'])].sort() }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [canvas, edges, focusId, lit])

  return (
    <section
      role="group"
      aria-label="Focused neighbourhood"
      className="flex min-w-0 flex-col gap-2"
    >
      <div className="relative min-w-0 overflow-hidden rounded-xl border border-outline-variant/40 bg-surface-container/40">
        <MemoryGraph
          data={canvas}
          focusRef={focusId}
          hopDepth={hopDepth}
          onSelectRef={onSelect}
          boxHeight={boxHeight}
          nodeNoun="item"
          emptyHint="This document is not in the graph — the graph covers items that have been indexed."
        />
        {
}
        <div className="absolute bottom-11 left-3 right-14 flex flex-nowrap items-center gap-1.5 overflow-x-auto pb-1">
          <div className="w-[8.5rem] shrink-0">
            <Select
              value={linkType}
              onChange={setLinkType}
              options={linkTypeOptions}
              ariaLabel="Filter links by type"
            />
          </div>
        </div>
        {
}
        <div data-type="caption" className="absolute right-3 top-3 flex items-center gap-2 rounded-pill bg-surface-high/90 px-2 py-1 backdrop-blur">
          <span className="text-on-surface-low">Focus · hops</span>
          <Segmented
            size="sm"
            ariaLabel="Focus · hops"
            value={String(hopDepth)}
            onChange={(k) => setHopDepth(Number(k))}
            options={HOP_DEPTHS.map((d) => ({ key: String(d), label: String(d) }))}
          />
          {onShowAll && (
            <TextLink size="xs" ink="emphasis" onClick={onShowAll}>
              ↺ show all
            </TextLink>
          )}
        </div>
      </div>

      {
}
      <div className="min-w-0">
        <div data-type="caption" className="mb-1 text-on-surface-low uppercase tracking-wide">What links here</div>
        {focusNode && (
          <div data-type="caption" className="mb-1 flex flex-wrap items-baseline gap-x-2">
            <span className="min-w-0 break-words text-on-surface">{focusNode.name || focusNode.id}</span>
            {focusNode.type && <span className="whitespace-nowrap text-on-surface-low">{focusNode.type}</span>}
          </div>
        )}
        {neighbours.length === 0 ? (
          <div data-type="caption" className="mt-2 text-on-surface-low italic">
            {hasAnyLink
              ? `Nothing links here within ${hopDepth} hop${hopDepth === 1 ? '' : 's'} — widen the hops, or clear the link filter.`
              : 'No links yet — either nothing scored close enough, or the graph has not covered this document.'}
          </div>
        ) : (
          <div className="mt-2 flex flex-col gap-1 border-t border-outline-variant/30 pt-2">
            {neighbours.map((n) => (
              <div key={n.id} data-type="caption" className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                {n.types.map((t) => (
                  <span
                    key={t}
                    className="whitespace-nowrap rounded bg-surface-high px-1.5 py-0.5 uppercase tracking-wide text-on-surface-low"
                  >
                    {t.replace(/_/g, ' ')}
                  </span>
                ))}
                <span className="min-w-0 break-words text-on-surface-var">{n.label}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  )
}
