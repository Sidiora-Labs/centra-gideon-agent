
export interface DerivedProp {
  name: string
  type: string
  required: boolean
}

export interface ExtractedUiProps {
  components: Record<string, DerivedProp[]>
  files: Record<string, string[]>
}

export function extractUiProps(uiDir: string): ExtractedUiProps
