import type { ReactNode } from 'react'
import type { ToolSegment } from './chatTypes'
import { UISpecView } from '../../shared/ui/assistant-ui/generative/UISpecView'
import { donorStructures } from '../../shared/ui/assistant-ui/generative/donorStructures'
import { bindDonorUISpec, type GenerativeTemplate, type LiveUISpec } from '../../shared/ui/assistant-ui/generative/uispec'
import type { ActionCapabilities } from '../../shared/ui/assistant-ui/generative/actions'

export type UISpecProducerContracts = Readonly<Record<string, readonly GenerativeTemplate[]>>
export const connectedUISpecContracts: UISpecProducerContracts = {
  visualize: donorStructures.map(item => item.slug as GenerativeTemplate),
}

const VISUALIZE_PREFIX = 'Show this to the user by embedding the widget block below in your reply:\n\n'
const VISUALIZE_WIDGET = /^<widget kind="uispec" title="[^"\r\n]{1,256}">\n([^\r\n]{1,65536})\n<\/widget>$/

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function sameJson(left: unknown, right: unknown, depth = 0): boolean {
  if (depth > 8) return false
  if (!object(left) && !Array.isArray(left)) return Object.is(left, right)
  if (Array.isArray(left)) return Array.isArray(right) && left.length <= 128 && left.length === right.length &&
    left.every((item, index) => sameJson(item, right[index], depth + 1))
  if (!object(right)) return false
  const keys = Object.keys(left)
  return keys.length <= 128 && keys.length === Object.keys(right).length &&
    keys.every(key => Object.hasOwn(right, key) && sameJson(left[key], right[key], depth + 1))
}

function visualizePayload(seg: ToolSegment): unknown {
  if (seg.tool !== 'visualize' || !seg.output?.startsWith(VISUALIZE_PREFIX)) return null
  const match = VISUALIZE_WIDGET.exec(seg.output.slice(VISUALIZE_PREFIX.length))
  if (!match) return null
  let payload: unknown
  try { payload = JSON.parse(match[1]) } catch { return null }
  if (!object(payload) || Object.keys(payload).length !== 4 ||
      !['schemaVersion', 'template', 'recordId', 'bindings'].every(key => Object.hasOwn(payload, key))) return null
  if (seg.input !== undefined || seg.inputObj !== undefined) {
    let input: unknown = seg.inputObj
    if (input === undefined) {
      try { input = JSON.parse(seg.input!) } catch { return null }
    }
    if (!object(input) || !object(input.data) || !sameJson(payload, input.data.generative_ui)) return null
  }
  return payload
}

export function resolveToolUISpec(seg: ToolSegment, contracts: UISpecProducerContracts): LiveUISpec | null {
  if (!seg.done || seg.ok === false || seg.truncated || !seg.output || seg.output.length > 1_000_000) return null
  const allowed = contracts[seg.tool]
  if (!allowed?.length) return null
  let payload: unknown = visualizePayload(seg)
  if (payload === null) {
    let result: unknown
    try { result = JSON.parse(seg.output) } catch { return null }
    if (!object(result)) return null
    payload = result.generative_ui
  }
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
