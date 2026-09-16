import type { PlanDraft, PlanStep } from './planStream'

export interface PlanNames { title?: string; description?: string; labels?: Record<string, string> }
export interface NamedPlan { title: string; description: string; labels: Record<string, string> }

const words = (value: string) => value.replace(/[_-]+/g, ' ').trim()
const preferred = (...values: (string | undefined)[]) => values.map((value) => value?.trim() ?? '').find(Boolean) ?? ''
const headline = (value: string) => value.length <= 60 ? value : value.slice(0, 59).trimEnd() + '…'

export function fallbackLabel(step: PlanStep): string {
  const candidate = preferred(step.label, step.role, step.target, words(step.kind ?? ''))
  if (candidate) return headline(candidate)
  const id = words(step.id)
  return id ? id.charAt(0).toUpperCase() + id.substring(1) : 'Step'
}

export function fallbackTitle(draft: PlanDraft, goal = ''): string {
  for (const line of goal.split('\n')) {
    if (line.trim()) return headline(line.trim().replace(/[.!?,;:]+$/, ''))
  }
  return draft.steps.length === 0 ? 'Plan' : `Plan · ${draft.steps.length} step${draft.steps.length === 1 ? '' : 's'}`
}

export function fallbackDescription(draft: PlanDraft): string {
  const labels = draft.steps.map(fallbackLabel).filter(Boolean)
  switch (labels.length) {
    case 0: return 'No steps yet.'
    case 1: return `A single step: ${labels[0]}.`
    default: return `${labels.length} steps: ${labels.slice(0, -1).join(', ')}, then ${labels[labels.length - 1]}.`
  }
}

export function resolvePlanNames(draft: PlanDraft, names: PlanNames | null | undefined, goal = ''): NamedPlan {
  return {
    title: preferred(names?.title, draft.title, fallbackTitle(draft, goal)),
    description: preferred(names?.description, draft.description, fallbackDescription(draft)),
    labels: Object.fromEntries(draft.steps.map((step) => [step.id, preferred(names?.labels?.[step.id], step.label, fallbackLabel(step))])),
  }
}
