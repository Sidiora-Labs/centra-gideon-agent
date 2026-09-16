
export type SourceKind = 'native' | 'bundled' | 'first-party' | 'local' | 'git'

export interface Provenance {
  label: string
  title: string
}

const PROVENANCE: Record<string, Provenance> = {
  native: { label: 'built-in', title: 'Ships with Gideon — it was installed with the product, not from a source you added.' },
  bundled: { label: 'built-in', title: 'Ships with Gideon — it was installed with the product, not from a source you added.' },
  'first-party': { label: 'first-party', title: 'From the first-party apps directory on this machine.' },
  local: { label: 'local', title: 'From a folder on this machine that you added as a Store source.' },
  git: { label: 'git', title: 'From a remote git source. These bytes are fetched over the network.' },
}

const PLATFORM: Provenance = {
  label: 'platform',
  title: 'A core capability of Gideon itself — it cannot be removed or turned off.',
}

export function provenance(input: { sourceKind?: string | null; locked?: boolean }): Provenance | null {
  if (input.locked) return PLATFORM
  const kind = (input.sourceKind ?? '').trim()
  return PROVENANCE[kind] ?? null
}
