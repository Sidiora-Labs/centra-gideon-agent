import { KIND_TO_TEMPLATE, templateForKind } from './containerKey'


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
