
export interface FoldableRun {
  id?: string
  run_id?: string
}

export interface RunFold<T extends FoldableRun> {
  did: T[]
  suppressed: T[]
}

export function partitionRuns<T extends FoldableRun>(runs: T[], didIds: string[]): RunFold<T> {
  const did = new Set(didIds)
  const isDid = (r: T) => !r.id || did.has(r.id)
  return { did: runs.filter(isDid), suppressed: runs.filter((r) => !isDid(r)) }
}
