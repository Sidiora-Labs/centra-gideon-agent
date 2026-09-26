import type { EmbedProps } from '../../content/contentTypes'
import { UISpecView } from './UISpecView'
import { bindDonorUISpec, type LiveUISpec } from './uispec'

export function resolveUISpecContent(content: string): LiveUISpec | null {
  if (!content || content.length > 65_536) return null
  let parsed: unknown
  try { parsed = JSON.parse(content) } catch { return null }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
  const data = parsed as Record<string, unknown>
  if (data.schemaVersion !== 1 || Object.keys(data).some(key =>
    !['schemaVersion', 'template', 'recordId', 'bindings'].includes(key))) return null
  return bindDonorUISpec({ template: data.template, producer: 'model:visualize',
    recordId: data.recordId, bindings: data.bindings })
}

export function UISpecEmbed({ content, streaming }: EmbedProps) {
  if (streaming) return null
  const spec = resolveUISpecContent(content)
  return spec ? <UISpecView spec={spec} /> : <p role="alert">Structured result unavailable: invalid or incomplete record.</p>
}
