import { BarChart3 } from 'lucide-react'
import type { RunSnapshotViewModel } from './runFold'

export function RunProgress({ vm }: { vm: RunSnapshotViewModel }) {
  const showBar = vm.phaseTotal > 0
  const showRoi = vm.bestScore !== null || vm.marginals.length > 0
  if (!showBar && !showRoi) return null

  const pct = vm.phaseTotal > 0 ? Math.round((vm.phaseDone / vm.phaseTotal) * 100) : 0
  const marginalMax = vm.marginals.length ? Math.max(...vm.marginals, 0.001) : 0

  return (
    <>
      {showBar && (
        <div className="h-1 w-full bg-surface-container" title={`${vm.phaseDone} of ${vm.phaseTotal} stages done`}>
          <div className="h-full transition-[width] duration-500"
            style={{ width: `${pct}%`, background: vm.parked ? 'var(--color-warn)' : 'var(--color-primary)' }} />
        </div>
      )}
      {showRoi && (
        <div className="flex items-center gap-2 border-t border-outline-variant/30 px-3 py-1.5">
          {vm.bestScore !== null && (
            <span data-type="caption" className="shrink-0 inline-flex items-center gap-1 text-on-surface-var" title="Best rubric score (monotonic ratchet)">
              <BarChart3 size={11} className="text-primary" /> best {vm.bestScore.toFixed(1)}
              {vm.lastScore !== null && vm.lastScore !== vm.bestScore && <span className="text-on-surface-low/70">· last {vm.lastScore.toFixed(1)}</span>}
            </span>
          )}
          {vm.marginals.length > 0 && (
            <span className="ml-auto inline-flex h-4 items-end gap-[2px]" title="Marginal value per recent cycle (returns trend)">
              {vm.marginals.map((mv, i) => (
                <span key={i} className="w-[3px] rounded-t-[1px] bg-primary/70"
                  style={{ height: `${Math.max(8, Math.round((Math.max(0, mv) / marginalMax) * 100))}%` }} />
              ))}
            </span>
          )}
        </div>
      )}
    </>
  )
}
