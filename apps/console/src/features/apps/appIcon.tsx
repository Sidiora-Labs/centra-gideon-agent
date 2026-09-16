import * as Lucide from 'lucide-react'
import { Blocks, icons as lucideIconRegistry, type LucideIcon } from 'lucide-react'

const LUCIDE_ICON_COMPONENTS: ReadonlySet<unknown> = new Set(Object.values(lucideIconRegistry))

export function AppIcon({ name, size = 18 }: { name?: string; size?: number }) {
  const Icon = resolveAppIcon(name)
  return <Icon size={size} />
}

export function resolveAppIcon(name?: string): LucideIcon {
  if (!name || !/^[A-Za-z]/.test(name)) return Blocks
  const candidate = (Lucide as unknown as Record<string, unknown>)[name]
  return LUCIDE_ICON_COMPONENTS.has(candidate) ? (candidate as LucideIcon) : Blocks
}
