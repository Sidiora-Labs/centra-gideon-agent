import type { DialogField } from '../../shared/ui/dialog'
import type { WorkflowInputParam } from '../../shared/data/api'


export function inputFields(inputs: Record<string, WorkflowInputParam> | undefined): DialogField[] {
  const entries = Object.entries(inputs ?? {})
  const rank = (p: WorkflowInputParam) => (p.required ? 0 : 1)
  return entries
    .map(([name, param], i) => ({ name, param, i }))
    .sort((a, b) => rank(a.param) - rank(b.param) || a.i - b.i)
    .map(({ name, param }) => ({
      name,
      label: param.required ? `${labelFor(name)} *` : labelFor(name),
      placeholder: param.help || '',
      initial: param.default === undefined || param.default === null ? '' : String(param.default),
      type: (param.help?.length ?? 0) > 90 ? ('textarea' as const) : ('text' as const),
      required: !!param.required,
    }))
}

export function labelFor(name: string): string {
  const words = name.replace(/[_-]+/g, ' ').trim()
  return words ? words[0].toUpperCase() + words.slice(1) : name
}

export function coerceInputs(
  answers: Record<string, string>,
  inputs: Record<string, WorkflowInputParam> | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [name, raw] of Object.entries(answers)) {
    const param = (inputs ?? {})[name]
    const text = (raw ?? '').trim()
    if (!text && !param?.required) continue
    switch (param?.type) {
      case 'number': {
        const n = Number(text)
        out[name] = Number.isFinite(n) ? n : text
        break
      }
      case 'boolean':
        out[name] = /^(true|yes|1|on)$/i.test(text)
        break
      default:
        out[name] = text
    }
  }
  return out
}

export function startsWithoutInput(
  inputs: Record<string, WorkflowInputParam> | undefined,
): boolean {
  return !Object.values(inputs ?? {}).some((p) => p.required)
}

export function validateDeepLinkParams(
  raw: Record<string, string> | URLSearchParams,
  inputs: Record<string, WorkflowInputParam> | undefined,
): { accepted: Record<string, string>; rejected: string[] } {
  const declared = new Set(Object.keys(inputs ?? {}))
  const entries: Array<[string, string]> =
    raw instanceof URLSearchParams ? Array.from(raw.entries()) : Object.entries(raw ?? {})
  const accepted: Record<string, string> = {}
  const rejected: string[] = []
  for (const [key, value] of entries) {
    if (key === 'template') continue
    if (!declared.has(key)) {
      rejected.push(key)
      continue
    }
    if (value === '') continue
    accepted[key] = value
  }
  return { accepted, rejected: rejected.sort() }
}

export function missingRequired(
  params: Record<string, string>,
  inputs: Record<string, WorkflowInputParam> | undefined,
): string[] {
  return Object.entries(inputs ?? {})
    .filter(([name, param]) => param.required && !params[name])
    .map(([name]) => name)
    .sort()
}
