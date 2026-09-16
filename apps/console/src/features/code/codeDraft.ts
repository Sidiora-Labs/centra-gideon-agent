import type { CodeClassification } from '../../shared/data/api'

export interface CodeDraft {
  projectId: string
  classification: CodeClassification
  rigor: string
  attended: boolean
}
