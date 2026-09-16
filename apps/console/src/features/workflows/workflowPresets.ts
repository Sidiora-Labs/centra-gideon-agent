import { Code2, Compass, Palette, Target, Telescope, type LucideIcon } from 'lucide-react'
import type { PresetDef } from '../../shared/ui/PresetEmptyState'
import type { WorkflowDefSummary } from '../../shared/data/api'
import { templateForKind } from './containerKey'


const KIND_CARDS: Array<{ kind: string; icon: LucideIcon; title: string }> = [
  { kind: 'code', icon: Code2, title: 'Work on code' },
  { kind: 'research', icon: Telescope, title: 'Research a topic' },
  { kind: 'design', icon: Palette, title: 'Design something' },
  { kind: 'goal', icon: Target, title: 'Pursue a goal' },
  { kind: 'general', icon: Compass, title: 'Plan a project' },
]

export type WorkflowPrefill = string

export function workflowPresets(defs: WorkflowDefSummary[]): PresetDef<WorkflowPrefill>[] {
  const byName = new Map(defs.map((d) => [d.name, d]))
  const out: PresetDef<WorkflowPrefill>[] = []
  for (const card of KIND_CARDS) {
    const template = templateForKind(card.kind)
    const def = template ? byName.get(template) : undefined
    if (!def) continue
    out.push({
      id: card.kind,
      icon: card.icon,
      title: card.title,
      summary: def.name,
      description: def.description,
      prefill: def.name,
    })
  }
  return out
}
