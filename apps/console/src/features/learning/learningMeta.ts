import { Sparkles, BookOpen, Workflow, GitPullRequest, Trash2, ArrowUpDown, FileText, HelpCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { measurement } from './learningDisplay'
import type { LearningRow, StagingDay } from '../../shared/data/api'

const KIND_META: Record<string, { label: string; icon: LucideIcon }> = {
  skill: { label: 'Skill', icon: Sparkles },
  lesson_batch: { label: 'Lessons', icon: BookOpen },
  template: { label: 'Template', icon: Workflow },
  template_diff: { label: 'Template edit', icon: GitPullRequest },
  retirement: { label: 'Retirement', icon: Trash2 },
  tier_migration: { label: 'Tier change', icon: ArrowUpDown },
  project_instruction: { label: 'Project instruction', icon: BookOpen },
  project_file: { label: 'Project file', icon: FileText },
  project_skill: { label: 'Project skill', icon: Sparkles },
  knowledge_draft: { label: 'Knowledge draft', icon: BookOpen },
}

export function kindLabel(kind: string): string {
  const metadata = KIND_META[kind]
  return metadata ? metadata.label : kind
}
export function kindIcon(kind: string): LucideIcon {
  const metadata = KIND_META[kind]
  return metadata ? metadata.icon : HelpCircle
}

export const TIER_TONE: Record<string, string> = {
  low: 'var(--color-on-surface-var)',
  review: 'var(--color-info)',
  manual_only: 'var(--color-warn)',
}
export const TIER_LABEL: Record<string, string> = {
  low: 'Low risk',
  review: 'Review',
  manual_only: 'Manual only',
}
export function tierTone(tier: string): string {

  return Object.hasOwn(TIER_TONE, tier) ? TIER_TONE[tier] : 'var(--color-warn)'
}
export function tierLabel(tier: string): string {
  return Object.hasOwn(TIER_LABEL, tier) ? TIER_LABEL[tier] : 'Unscored'
}

export function bulkBlockedReason(row: LearningRow): string {
  if (row.bulk_acceptable) return ''
  const reasons: [boolean, string][] = [
    [!row.renderable, 'missing provenance — cannot be shown weighably'],
    [row.risk_tier === 'manual_only', 'destructive edits are never bulk-accepted'],
    [!row.manifest_valid, 'its change manifest is invalid'],
    [row.evidence_refs.length === 0, 'no evidence to check'],
  ]
  return reasons.find(([applies]) => applies)?.[1] ?? 'not eligible for bulk accept'
}

const EVIDENCE_GRADE: Record<string, string> = {
  ablation: 'measured on/off',
  causal: 'measured (controlled study)',
  correlated: 'correlated',
  anecdotal: 'anecdotal',
}

export function evidenceLabel(row: LearningRow): string {
  const refs = row.evidence_refs.length
  return refs === 0 ? 'no evidence' : [
    `${refs} evidence ref${refs === 1 ? '' : 's'}`,
    EVIDENCE_GRADE[row.evidence_strength] ?? 'ungraded',
  ].join(' · ')
}

export function gateScore(value: number | null): string {
  return measurement(value, 3)
}

export function gateLabel(row: LearningRow): string {
  const gate = row.gate
  if (gate?.state !== 'gated') return ['ungated', gate?.reason ? ` — ${gate.reason}` : ''].join('')
  const clauses = [`gate ${gateScore(gate.before)} → ${gateScore(gate.after)}`]
  if (gate.delta !== null) clauses.push(` (${gate.delta > 0 ? '+' : ''}${gate.delta.toFixed(3)})`)
  clauses.push(` over ${gate.scenarios} gate scenario${gate.scenarios === 1 ? '' : 's'}`)
  if (gate.halted) clauses.push(' · stopped early on the eval budget')
  return clauses.join('')
}

export function gateRegressed(row: LearningRow): boolean {
  const gate = row.gate
  return gate?.state === 'gated' ? typeof gate.delta === 'number' && gate.delta < 0 : false
}

export function replayScore(value: number | null): string {
  return measurement(value, 3)
}

const VERDICT_CLAUSE: Record<string, string> = {
  improved: 'improved on your captured turns',
  neutral: 'no measurable difference on your captured turns',
  regressed: 'made things worse on your captured turns',
}

export function replayLabel(row: LearningRow): string {
  const replay = row.replay
  if (replay?.state !== 'replayed') return ['not replayed', replay?.reason ? ` — ${replay.reason}` : ''].join('')
  const clauses = [`replay ${replayScore(replay.baseline_mean)} → ${replayScore(replay.candidate_mean)}`]
  const verdict = VERDICT_CLAUSE[replay.verdict]
  if (verdict) clauses.push(` — ${verdict}`)
  clauses.push(` over ${replay.scored} replayed case${replay.scored === 1 ? '' : 's'}`)
  if (replay.rejected) clauses.push(` · ${replay.rejected} case${replay.rejected === 1 ? '' : 's'} rejected`)
  if (replay.deferred) clauses.push(' · deferred on the replay budget')
  return clauses.join('')
}

export function replayRegressed(row: LearningRow): boolean {
  const replay = row.replay
  return replay?.state === 'replayed' ? replay.verdict === 'regressed' : false
}

export type DayState = 'silent' | 'error' | 'produced' | 'ok'

export function dayState(day: StagingDay): DayState {
  const states: [boolean, DayState][] = [[day.passes === 0, 'silent'], [day.errors > 0, 'error'], [day.produced > 0, 'produced']]
  return states.find(([matches]) => matches)?.[1] ?? 'ok'
}

export const DAY_TONE: Record<DayState, string> = {
  silent: 'var(--color-warn)',
  error: 'var(--color-danger)',
  produced: 'var(--color-primary)',
  ok: 'var(--color-on-surface-low)',
}

export const DAY_HINT: Record<DayState, string> = {
  silent: 'No capture pass ran — this is the gap an aggregate view cannot see',
  error: 'A capture pass errored',
  produced: 'Produced proposals',
  ok: 'Ran, produced nothing',
}

export function dayLabel(day: string): string {
  const parts = day.split('-').map(Number)
  if (parts.length < 3 || !parts.slice(0, 3).every(Boolean)) return day
  const local = new Date(parts[0], parts[1] - 1, parts[2])
  return local.toLocaleDateString(undefined, { weekday: 'short' })
}
