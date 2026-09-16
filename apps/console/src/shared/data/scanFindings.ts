import glossSource from '../../../../../runtime/gideon/security/scan_rule_gloss.json'


export const SCAN_FINDINGS_SHOWN = 8

export function hiddenFindingsNote(total: number): string | null {
  const hidden = total - SCAN_FINDINGS_SHOWN
  if (hidden <= 0) return null
  return `+${hidden} more finding${hidden === 1 ? '' : 's'} not shown`
}

export const SCAN_RULE_GLOSS: Record<string, string> = Object.fromEntries(
  Object.entries(glossSource).filter(
    (e): e is [string, string] => !e[0].startsWith('_') && typeof e[1] === 'string',
  ),
)

export function ruleGloss(rule: string): string {
  return SCAN_RULE_GLOSS[rule] ?? ''
}
