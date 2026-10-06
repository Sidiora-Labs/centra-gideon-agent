export type DenyConsequence = 'declines' | 'carries_on' | 'ends'
export function decodeDenyConsequence(value: unknown): DenyConsequence | undefined {
  return value === 'declines' || value === 'carries_on' || value === 'ends' ? value : undefined
}
export function denyConsequenceText(value?: DenyConsequence): string {
  return value === 'declines' ? 'Deny declines this step; the agent can continue.'
    : value === 'carries_on' ? 'Deny ends the agent’s turn; Gideon asks it to continue without this step.'
    : value === 'ends' ? 'Deny ends the agent’s turn. The continuation limit has been reached.' : ''
}
