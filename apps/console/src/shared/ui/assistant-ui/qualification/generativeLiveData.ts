import { donorStructures } from '../generative/donorStructures'
import type { GenerativeTemplate } from '../generative/uispec'

export function boundRecord(template: GenerativeTemplate) {
  const definition = donorStructures.find((item) => item.slug === template)
  if (!definition) throw new Error(`Unknown test template: ${template}`)
  const bindings: Record<string, unknown> = {}
  function collect(value: unknown): void {
    if (Array.isArray(value)) { value.forEach(collect); return }
    if (!value || typeof value !== 'object') return
    const node = value as Record<string, unknown>
    if (typeof node.$bind === 'string') {
      const path = node.$bind
      bindings[path] = path.endsWith('.src') ? 'https://example.test/live-image.png' :
        path.endsWith('.tone') ? 'success' :
        node.$kind === 'array' ? [] : node.$kind === 'number' ? 42 :
          node.$kind === 'boolean' ? true : `Live ${path}`
      return
    }
    Object.values(node).forEach(collect)
  }
  collect(definition.tree)
  return { template, producer: `tool:${template}`, recordId: `${template}-record-42`, bindings }
}
