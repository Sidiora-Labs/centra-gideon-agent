export interface WidgetAnnotation {
  selector: string
  tag: string
  outerHTML: string
  parentContext: string
  note: string
}
export const MAX_ANNOTATIONS = 12
const limits = { selector: 240, tag: 24, outerHTML: 400, parentContext: 120, note: 400 } as const
const singleLine = (input: unknown, limit: number) => typeof input === 'string' ? input.trim().replace(/\s+/g, ' ').slice(0, limit) : ''

export function readAnnotation(input: unknown): WidgetAnnotation | null {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return null
  const record = input as Record<string, unknown>
  const fields = Object.fromEntries(Object.entries(limits).map(([key, limit]) => [key, singleLine(record[key], limit)])) as unknown as WidgetAnnotation
  return fields.selector ? { ...fields, tag: fields.tag.toLowerCase(), note: '' } : null
}

export function composeCorrectionBody(annotations: WidgetAnnotation[]): string {
  const entries = annotations.slice(0, MAX_ANNOTATIONS).flatMap((annotation, index) => {
    const detail = [
      [`${index + 1}. selector`, annotation.selector],
      ['   within', annotation.parentContext],
      ['   element', annotation.outerHTML],
      ['   change', singleLine(annotation.note, limits.note) || '(no note — the user marked this element without describing the change)'],
    ]
    return detail.filter(([, value]) => value).map(([label, value]) => `${label}: ${value}`)
  })
  return ['```corrections', ...entries, '```'].join('\n')
}

export function composeCorrectionDirective(annotations: WidgetAnnotation[]): string {
  const count = Math.min(annotations.length, MAX_ANNOTATIONS)
  return `correction: ${count} element${count === 1 ? '' : 's'} marked — regenerate the artifact applying every change below, and change nothing else\n${composeCorrectionBody(annotations)}`
}
