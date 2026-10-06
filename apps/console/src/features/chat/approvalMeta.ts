
import type { ApprovalSegment } from './chatTypes'

export type ApprovalRisk = NonNullable<ApprovalSegment['risk']>

export interface BlastRadius {
  writes: boolean
  network: boolean
  shell: boolean
  readOnly: boolean
  saysReadOnly?: boolean
}

export interface BlastRadiusInput {
  tool: string
  blastRadius?: BlastRadius
  risk?: ApprovalRisk
  readOnlyCommand?: boolean
}

export const RISK_ESTABLISHES_READ_ONLY: Record<ApprovalRisk, boolean> = {
  safe: true,
  caution: false,
  destructive: false,
  unchecked: false,
}

export function decodeBlastRadius(value: unknown): BlastRadius | undefined {
  if (!value || typeof value !== 'object') return undefined
  const radius = value as Record<string, unknown>
  const keys = ['writes', 'network', 'shell', 'readOnly', 'saysReadOnly'] as const
  if (!keys.some(key => radius[key] === true)) return undefined
  return Object.fromEntries(keys.map(key => [key, radius[key] === true])) as unknown as BlastRadius
}

export function deriveBlastRadius(input: BlastRadiusInput): BlastRadius | undefined {
  return decodeBlastRadius(input.blastRadius)
}


export interface BlastRadiusFacet {
  key: keyof BlastRadius
  label: string
  detail: string
}

const FACET_COPY: Record<keyof BlastRadius, { label: string; detail: string }> = {
  writes: { label: 'Writes files', detail: 'Can create or change files on this machine.' },
  shell: { label: 'Runs a command', detail: 'Can execute a command on this machine.' },
  network: { label: 'Uses the network', detail: 'Can reach the network from this machine.' },
  saysReadOnly: { label: 'Server says it only reads', detail: 'The server labels this tool read-only. That claim belongs to its reviewed definition; new or changed definitions ask until reviewed.' },
  readOnly: { label: 'Reads only', detail: 'Established as a read: no change was established.' },
}

export const BLAST_RADIUS_FACET_ORDER: readonly (keyof BlastRadius)[] = [
  'writes', 'shell', 'network', 'saysReadOnly', 'readOnly',
]

export function establishedFacets(radius: BlastRadius | undefined): BlastRadiusFacet[] {
  if (!radius) return []
  return BLAST_RADIUS_FACET_ORDER.filter((k) => radius[k]).map((k) => ({ key: k, ...FACET_COPY[k] }))
}

export function blastRadiusLine(radius: BlastRadius | undefined): string {
  const facets = establishedFacets(radius)
  if (facets.length === 0) return ''
  return facets.map((f) => f.label.toLowerCase()).join(', ')
}
