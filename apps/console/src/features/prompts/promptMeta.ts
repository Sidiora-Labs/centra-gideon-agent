import type { PromptVarType, PromptVariable } from '../../shared/data/api'
export interface VarTypeMeta { key: PromptVarType; label: string }
export const VAR_TYPES: VarTypeMeta[] = [
  { key: 'text', label: 'Text (line)' },
  { key: 'textarea', label: 'Text (block)' },
  { key: 'number', label: 'Number' },
  { key: 'boolean', label: 'Yes / no' },
  { key: 'select', label: 'Choice' },
]

const TYPE_ALIASES: Record<string, PromptVarType> = {
  string: 'text', str: 'text', text: 'text',
  longtext: 'textarea', long_text: 'textarea', multiline: 'textarea', textarea: 'textarea',
  numeric: 'number', integer: 'number', int: 'number', float: 'number', number: 'number',
  bool: 'boolean', boolean: 'boolean',
  select: 'select', multiselect: 'select', enum: 'select',
}
const TEMPLATE_EXPRESSION = /\{\{\s*(.*?)\s*\}\}/gs
const TYPE_DECLARATION = /^([a-zA-Z_][\w.]*)\s*::\s*(.+)$/
const BARE_VARIABLE = /^([a-zA-Z_][a-zA-Z0-9_]*)$/

const SOURCE_COLORS: Record<string, string> = { user: 'var(--color-primary)', marketplace: 'var(--color-info)' }
export function isReadOnly(source?: string): boolean { return !['', 'user'].includes(source ?? '') }
export function sourceTone(source?: string): string { return SOURCE_COLORS[source || 'user'] ?? 'var(--color-on-surface-low)' }
export function sourceLabel(source?: string, tags?: string[]): string {
  const origin = source || 'user'
  return origin === 'user' && tags?.includes('bundled') ? 'bundled' : origin
}
export function variableTypeLabel(type: PromptVarType): string { return VAR_TYPES.find(meta => meta.key === type)?.label ?? type }

function parseTypeDeclaration(suffix: string): { type: PromptVarType; options: string[] } {
  const source = suffix.trim()
  const bracket = /\[(.*)\]/.exec(source)
  const options = bracket ? bracket[1].split(',').map(option => option.trim()).filter(Boolean) : []
  const typePart = (bracket ? source.slice(0, bracket.index).replace(/[: ]+$/, '') : source).trim()
  const canonical = typePart ? TYPE_ALIASES[typePart.toLowerCase()] : undefined
  return { type: canonical ?? (options.length ? 'select' : 'text'), options }
}

export function detectInlineVariables(content: string): PromptVariable[] {
  const variables: PromptVariable[] = []
  const seen = new Set<string>()
  for (const match of content.matchAll(TEMPLATE_EXPRESSION)) {
    const declaration = TYPE_DECLARATION.exec(match[1].trim())
    if (!declaration) continue
    const name = declaration[1]
    if (name.includes('.') || seen.has(name)) continue
    seen.add(name)
    const parsed = parseTypeDeclaration(declaration[2])
    variables.push({ name, type: parsed.type, ...(parsed.options.length ? { options: parsed.options } : {}) })
  }
  return variables
}

export function mergePromptVariables(...groups: Array<PromptVariable[] | undefined>): PromptVariable[] {
  const seen = new Set<string>()
  return groups.flatMap(group => (group ?? []).filter(variable => {
    if (seen.has(variable.name)) return false
    seen.add(variable.name)
    return true
  }))
}

export function promptVars(prompt: { variables?: PromptVariable[]; content?: string }): PromptVariable[] {
  return mergePromptVariables(prompt.variables, detectInlineVariables(prompt.content ?? ''))
}
function templateTokens(content: string, pattern: RegExp): string[] {
  return [...new Set(Array.from(content.matchAll(pattern), match => match[1]))]
}
export function detectPlaceholders(content: string): string[] {
  const names: string[] = []
  const seen = new Set<string>()
  for (const match of content.matchAll(TEMPLATE_EXPRESSION)) {
    const expression = match[1].trim()
    const name = TYPE_DECLARATION.exec(expression)?.[1] ?? BARE_VARIABLE.exec(expression)?.[1]
    if (!name || name.includes('.') || seen.has(name)) continue
    seen.add(name)
    names.push(name)
  }
  return names
}
export function detectIncludes(content: string): string[] { return templateTokens(content, /\{\{>\s*([a-zA-Z0-9_-]+)\s*\}\}/g) }
export function seedRenderValues(variables: PromptVariable[]): Record<string, unknown> {
  return Object.fromEntries(variables.map(variable => [variable.name, variable.default ?? (variable.type === 'boolean' ? false : '')]))
}
