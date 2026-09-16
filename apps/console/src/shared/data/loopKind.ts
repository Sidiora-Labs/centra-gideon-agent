import { Code2, Target, Palette, Telescope, Repeat, type LucideIcon } from 'lucide-react'
import type { LoopKind } from './api'

export interface LoopKindMeta { icon: LucideIcon; noun: string; short: string }

const LOOP_KIND_META: Record<LoopKind, LoopKindMeta> = {
  code: { icon: Code2, noun: 'Code', short: 'Code' },
  goal: { icon: Target, noun: 'Goal Loop', short: 'Goal' },
  design: { icon: Palette, noun: 'Design', short: 'Design' },
  research: { icon: Telescope, noun: 'Research', short: 'Research' },
  general: { icon: Repeat, noun: 'Loop', short: 'Loop' },
}

export function loopKindMeta(kind: string | undefined): LoopKindMeta {
  return (kind && LOOP_KIND_META[kind as LoopKind]) || LOOP_KIND_META.general
}
