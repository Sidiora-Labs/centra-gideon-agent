import { KIND_TO_TEMPLATE, templateForKind } from './containerKey'
import type { WorkflowDefSummary, WorkflowSurfacingRow } from '../../shared/data/api'


const KIND_CUES: Array<[kind: string, cues: string[]]> = [
  ['code', ['code', 'coding', 'implement', 'refactor', 'bug', 'fix', 'feature', 'endpoint', 'api', 'function', 'test', 'debug', 'compile', 'build']],
  ['design', ['design', 'ui', 'ux', 'mockup', 'wireframe', 'layout', 'component', 'visual', 'prototype', 'figma']],
  ['research', ['research', 'investigate', 'sources', 'literature', 'survey', 'compare', 'find out', 'deep dive', 'evidence', 'cite']],
  ['goal', ['goal', 'achieve', 'pursue', 'until', 'reach', 'objective']],
]

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function scoreKind(text: string, cues: string[]): number {
  const hay = ` ${text.toLowerCase()} `
  let hits = 0
  for (const cue of cues) {
    const re = new RegExp(`(^|\\W)${escapeRe(cue)}(\\W|$)`, 'i')
    if (re.test(hay)) hits += 1
  }
  return hits
}

export function intentKind(text: string): string {
  const cleaned = (text ?? '').trim()
  if (!cleaned) return ''
  let best = ''
  let bestScore = 0
  for (const [kind, cues] of KIND_CUES) {
    const score = scoreKind(cleaned, cues)
    if (score > bestScore) {
      best = kind
      bestScore = score
    }
  }
  return best
}

export function suggestTemplate(text: string, available: Iterable<string>): string {
  const have = available instanceof Set ? available : new Set(available)
  const kind = intentKind(text)
  if (kind) {
    const template = templateForKind(kind)
    if (template && have.has(template)) return template
  }
  return ''
}

export function availableSuggestions(available: Iterable<string>): Array<{ kind: string; template: string }> {
  const have = available instanceof Set ? available : new Set(available)
  return Object.keys(KIND_TO_TEMPLATE)
    .map((kind) => ({ kind, template: templateForKind(kind) }))
    .filter((s) => s.template && have.has(s.template))
}

const STOP_WORDS = new Set(['a', 'an', 'and', 'for', 'from', 'in', 'my', 'of', 'on', 'the', 'to', 'with', 'do', 'run', 'please', 'want'])

export function rankWorkflowDefinitions(
  intent: string,
  defs: WorkflowDefSummary[],
  surfacing: Record<string, WorkflowSurfacingRow> = {},
): Array<{ definition: WorkflowDefSummary; score: number; reason: string }> {
  const words = [...new Set((intent.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []).filter((word) => word.length > 2 && !STOP_WORDS.has(word)))]
  if (!words.length) return []
  const kind = intentKind(intent)
  const preferred = kind ? templateForKind(kind) : ''
  return defs.map((definition) => {
    const details = surfacing[definition.name]
    const fields: Array<[string, number, string]> = [
      [definition.name, 5, 'name'],
      [definition.tags.join(' '), 4, 'tags'],
      [details?.when_to_use ?? '', 3, 'when to use'],
      [definition.description, 2, 'description'],
      [details?.summary ?? '', 1, 'purpose'],
    ]
    let score = definition.name === preferred ? 3 : 0
    let reason = score ? 'matches the requested kind' : ''
    for (const [value, weight, label] of fields) {
      const haystack = new Set(value.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [])
      const matches = words.filter((word) => haystack.has(word)).length
      if (matches * weight > 0) {
        score += matches * weight
        if (!reason) reason = `matches ${label}`
      }
    }
    return { definition, score, reason }
  }).filter((candidate) => candidate.score > 0)
    .sort((a, b) => b.score - a.score || a.definition.name.localeCompare(b.definition.name))
}
