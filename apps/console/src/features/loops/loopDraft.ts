import type { UnifiedLoopClassification, Granularity } from '../../shared/data/api'

export interface LoopDraft {
  loopId: string
  classification: UnifiedLoopClassification
  rigor: string
  agent: string
  model: string
  provider?: string
  providerAgent?: string
  reasoningEffort?: string
  granularity: Granularity
  attended: boolean
}
