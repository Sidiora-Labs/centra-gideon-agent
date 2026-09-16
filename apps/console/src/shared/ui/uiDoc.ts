
export interface UiDocProp {
  name: string
  description: string
  type?: string
  required?: boolean
}

export interface UiDocPractice {
  guidance: boolean
  description: string
}

export interface UiDoc {
  name: string
  keywords: string[]
  description: string
  props: UiDocProp[]
  bestPractices: UiDocPractice[]
  anatomy: string[]
}
