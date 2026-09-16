import { Check, Loader2 } from 'lucide-react'
import { phaseForCycle } from './loopPhases'
import type { LoopFinding } from '../../shared/data/api'

export function RunPhaseTrail({ plan, activePhase, active, complete, findings, compact }: {
  plan: Record<string, unknown>[]
  activePhase: number
  active: boolean
  complete: boolean
  findings: LoopFinding[]
  compact?: boolean
}) {
  return (
    <div data-type="caption" className={`flex flex-wrap items-center ${compact ? 'gap-0.5' : 'gap-1'}`}>
      {plan.map((p, i) => {
        const on = i === activePhase && active
        const role = String(p.role || '').trim() || `Phase ${i + 1}`
        const done = complete || i < activePhase
        const doneCycles = findings.filter((f) => phaseForCycle(f.cycle, plan) === i).length
        const cls = on
          ? 'text-on-primary'
          : done ? 'text-on-surface' : 'bg-surface-container text-on-surface-var'
        const style = on
          ? { background: 'var(--color-primary)' }
          : done ? { background: 'color-mix(in srgb, var(--color-ok) 22%, transparent)' } : undefined
        return (
          <span key={i} className="inline-flex items-center gap-1">
            {i > 0 && <span className="text-on-surface-low">→</span>}
            <span className={`inline-flex items-center gap-1 rounded-pill ${compact ? 'px-1.5' : 'px-2'} h-5 ${cls}`} style={style}>
              {Array.from({ length: doneCycles }).map((_, k) => (
                <Check key={k} size={11} className="shrink-0" style={{ color: 'var(--color-ok)' }} />
              ))}
              {on && <Loader2 size={11} className="shrink-0 animate-spin" />}
              {role}
            </span>
          </span>
        )
      })}
    </div>
  )
}
