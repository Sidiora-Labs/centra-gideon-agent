import type { PromptVarType, PromptVariable } from '../../shared/data/api'
export interface VarTypeMeta { key: PromptVarType; label: string }
export const VAR_TYPES: VarTypeMeta[] = [
  { key: 'text', label: 'Text (line)' },
  { key: 'textarea', label: 'Text (block)' },
  { key: 'number', label: 'Number' },
  { key: 'boolean', label: 'Yes / no' },
  { key: 'select', label: 'Choice' },
]

const SOURCE_COLORS: Record<string, string> = { user: 'var(--color-primary)', marketplace: 'var(--color-info)' }
export function isReadOnly(source?: string): boolean { return !['', 'user', 'bundled'].includes(source ?? '') }
export function sourceTone(source?: string): string { return SOURCE_COLORS[source || 'user'] ?? 'var(--color-on-surface-low)' }
export function promptProvenance(row: { source?: string; tags?: string[] }): string {
  const origin = row.source || 'user'
  return origin === 'user' && row.tags?.includes('bundled') ? 'bundled' : origin
}
export function sourceLabel(source?: string, tags?: string[]): string { return promptProvenance({ source, tags }) }
export function promptVars(prompt: { variables?: PromptVariable[] }): PromptVariable[] { return prompt.variables ?? [] }
function templateTokens(content: string, pattern: RegExp): string[] {
  return [...new Set(Array.from(content.matchAll(pattern), match => match[1]))]
}
export function detectPlaceholders(content: string): string[] { return templateTokens(content, /\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}/g) }
export function detectIncludes(content: string): string[] { return templateTokens(content, /\{\{>\s*([a-zA-Z0-9_-]+)\s*\}\}/g) }
export function seedRenderValues(variables: PromptVariable[]): Record<string, unknown> {
  return Object.fromEntries(variables.map(variable => [variable.name, variable.default ?? (variable.type === 'boolean' ? false : '')]))
}
