export interface LitEdge {
  from: string
  to: string
}

export function litNeighbourhood(
  edges: readonly LitEdge[],
  focusId: string | null | undefined,
  hopDepth: number,
): Set<string> | null {
  if (!focusId) return null
  const adj = new Map<string, string[]>()
  for (const e of edges) {
    ;(adj.get(e.from) ?? adj.set(e.from, []).get(e.from)!).push(e.to)
    ;(adj.get(e.to) ?? adj.set(e.to, []).get(e.to)!).push(e.from)
  }
  const lit = new Set<string>([focusId])
  let frontier = [focusId]
  for (let hop = 0; hop < Math.max(1, hopDepth); hop++) {
    const next: string[] = []
    for (const id of frontier) for (const nb of adj.get(id) ?? []) if (!lit.has(nb)) { lit.add(nb); next.push(nb) }
    frontier = next
  }
  return lit
}
