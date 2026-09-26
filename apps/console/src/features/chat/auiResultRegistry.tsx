import type { ReactNode } from 'react'
import type { ToolSegment } from './chatTypes'
import { UISpecView } from '../../shared/ui/assistant-ui/generative/UISpecView'
import { bindDonorUISpec, type GenerativeTemplate, type LiveUISpec } from '../../shared/ui/assistant-ui/generative/uispec'
import type { ActionCapabilities } from '../../shared/ui/assistant-ui/generative/actions'

export type UISpecProducerContracts = Readonly<Record<string, readonly GenerativeTemplate[]>>

export function resolveToolUISpec(seg: ToolSegment, contracts: UISpecProducerContracts): LiveUISpec | null {
  if (!seg.done || seg.ok === false || seg.truncated || !seg.output || seg.output.length > 1_000_000) return null
  const allowed = contracts[seg.tool]
  if (!allowed?.length) return null
  let result: unknown
  try { result = JSON.parse(seg.output) } catch { return null }
  if (!result || typeof result !== 'object' || Array.isArray(result)) return null
  const payload = (result as Record<string, unknown>).generative_ui
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null
  const data = payload as Record<string, unknown>
  if (data.schemaVersion !== 1 || typeof data.template !== 'string' || !allowed.includes(data.template as GenerativeTemplate)) return null
  return bindDonorUISpec({ template: data.template, producer: `tool:${seg.tool}`, recordId: data.recordId, bindings: data.bindings })
}

export function renderAuiResult(seg: ToolSegment, contracts: UISpecProducerContracts,
  capabilities: ActionCapabilities = {}): ReactNode {
  const spec = resolveToolUISpec(seg, contracts)
  return spec ? <UISpecView key={`${seg.id}:${spec.recordId}`} spec={spec} capabilities={capabilities} /> : null
}
