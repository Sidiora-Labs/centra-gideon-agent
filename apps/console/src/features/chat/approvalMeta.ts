
import type { ApprovalSegment } from './chatTypes'

export type ApprovalRisk = NonNullable<ApprovalSegment['risk']>

export interface BlastRadius {
  writes: boolean
  network: boolean
  shell: boolean
  readOnly: boolean
}

export interface BlastRadiusInput {
  tool: string
  risk?: ApprovalRisk
  readOnlyCommand?: boolean
}

export const RISK_ESTABLISHES_READ_ONLY: Record<ApprovalRisk, boolean> = {
  safe: true,
  caution: false,
  destructive: false,
}

function riskEstablishesReadOnly(risk: ApprovalRisk | undefined): boolean {
  if (risk === undefined) return false
  return Object.prototype.hasOwnProperty.call(RISK_ESTABLISHES_READ_ONLY, risk)
    ? RISK_ESTABLISHES_READ_ONLY[risk]
    : false
}


const SHELL_HINTS = ['bash', 'shell', 'terminal', 'zsh', 'exec', 'spawn', 'command'] as const

const NETWORK_HINTS = ['web_', 'http', 'fetch', 'browse', 'download', 'upload', 'crawl', 'scrape', 'url'] as const

const DESTRUCTIVE_HINTS = ['delete', 'remove', 'destroy', 'drop_', 'purge', 'forget'] as const

const READ_VERB_HINTS = ['list', 'get', 'search', 'read', 'status', 'info', 'find', 'inspect', 'show', 'view'] as const

const WRITE_HINTS = [
  'write', 'edit', 'create', 'save', 'update', 'move', 'rename', 'append', 'remember',
  'set_', 'put_', 'install', 'deploy', 'subagent', 'schedule', 'notify', 'post_',
  'send', 'commit', 'push', 'generate',
] as const

function normalizeToolName(tool: string): string {
  const lowered = (tool || '').toLowerCase().trim()
  return lowered.includes('/') ? lowered.slice(lowered.lastIndexOf('/') + 1) : lowered
}

function hasAny(name: string, hints: readonly string[]): boolean {
  return hints.some((h) => name.includes(h))
}

export function deriveBlastRadius(input: BlastRadiusInput): BlastRadius | undefined {
  const name = normalizeToolName(input.tool)

  const shell = hasAny(name, SHELL_HINTS)
  const network = hasAny(name, NETWORK_HINTS)

  let writes = false
  let readVerb = false
  if (hasAny(name, DESTRUCTIVE_HINTS)) writes = true
  else if (hasAny(name, READ_VERB_HINTS)) readVerb = true
  else if (hasAny(name, WRITE_HINTS)) writes = true

  let readOnly = false
  if (input.readOnlyCommand === true) readOnly = true
  else if (input.readOnlyCommand !== false) readOnly = riskEstablishesReadOnly(input.risk) || readVerb
  if (writes) readOnly = false

  if (!writes && !network && !shell && !readOnly) return undefined
  return { writes, network, shell, readOnly }
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
  readOnly: { label: 'Reads only', detail: 'Established as a read: no change was established.' },
}

export const BLAST_RADIUS_FACET_ORDER: readonly (keyof BlastRadius)[] = [
  'writes', 'shell', 'network', 'readOnly',
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
