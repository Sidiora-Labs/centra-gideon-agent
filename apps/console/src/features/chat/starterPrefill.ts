import type { SessionTemplate } from '../../shared/data/api'

export interface StarterPrefill {
  name: string
  selection: { agent?: string; model?: string; reasoning?: string }
  input: string
}

export function starterPrefill(id: string, starters: SessionTemplate[]): StarterPrefill | null {
  const starter = starters.find((item) => item.id === id)
  if (!starter) return null
  return {
    name: starter.name,
    selection: {
      ...(starter.agent ? { agent: starter.agent } : {}),
      ...(starter.model ? { model: starter.model } : {}),
      ...(starter.reasoning_effort ? { reasoning: starter.reasoning_effort } : {}),
    },
    input: starter.first_prompt,
  }
}
