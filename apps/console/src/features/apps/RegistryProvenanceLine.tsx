import type { RegistryProvenance } from '../../shared/data/api'
import { registryProvenance } from '../../shared/data/provenance'

export function RegistryProvenanceLine({ registry }: { registry?: RegistryProvenance | null }) {
  const wording = registryProvenance(registry)
  if (!wording) return null
  return (
    <p data-testid="store-registry-provenance" data-type="label-s"
      className="text-on-surface-low break-words" title={wording.title}>
      {wording.label}
    </p>
  )
}
