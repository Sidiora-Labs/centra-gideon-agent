import * as Lucide from 'lucide-react'
import { Blocks, icons as lucideIconRegistry, type LucideIcon } from 'lucide-react'

let iconLookup: ReadonlyMap<string, LucideIcon | null> | undefined

function getIconLookup(): ReadonlyMap<string, LucideIcon | null> {
  if (iconLookup) return iconLookup

  const components: ReadonlySet<unknown> = new Set(Object.values(lucideIconRegistry))
  const lookup = new Map<string, LucideIcon | null>()
  for (const [name, candidate] of Object.entries(Lucide)) {
    if (!components.has(candidate)) continue
    const key = name.toLowerCase()
    const existing = lookup.get(key)
    if (existing === undefined) lookup.set(key, candidate as LucideIcon)
    else if (existing !== candidate) lookup.set(key, null)
  }
  iconLookup = lookup
  return lookup
}

export function AppIcon({ name, size = 18 }: { name?: string; size?: number }) {
  const Icon = resolveAppIcon(name)
  return <Icon size={size} />
}

export function resolveAppIcon(name?: string): LucideIcon {
  if (!name || !/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(name)) return Blocks
  return getIconLookup().get(name.toLowerCase()) ?? Blocks
}
