import type { AutonomyLadder, AutonomyType } from '../data/api'
import { rungMeta, rungReason } from '../data/rungs'
import { toneChipSkin } from '../theme/accent'

export function RungChip({ type, ladder = null }: { type: AutonomyType; ladder?: AutonomyLadder | null }) {
  const { icon: Icon, label, tone } = rungMeta(type.resolved_rung, ladder)
  const text = [label, ...(type.held_by_incident ? ['held'] : [])].join(' · ')
  return <span data-type="caption" data-rung={type.resolved_rung} title={rungReason(type, ladder)}
    className="inline-flex shrink-0 items-center gap-1 rounded-pill px-2 py-0.5 font-medium" style={toneChipSkin(tone, 14)}>
    <Icon size={11} className="shrink-0" aria-hidden /><span>{text}</span>
  </span>
}
