import type { AppCatalogEntry } from './api'

export interface CatalogAppLists {
  bundled?: AppCatalogEntry[]
  localApps?: AppCatalogEntry[]
  remoteApps?: AppCatalogEntry[]
  gitApps?: AppCatalogEntry[]
}

export function catalogApps(catalog: CatalogAppLists | null | undefined): AppCatalogEntry[] {
  const all = [
    ...(catalog?.bundled ?? []),
    ...(catalog?.localApps ?? []),
    ...(catalog?.remoteApps ?? []),
    ...(catalog?.gitApps ?? []),
  ]
  const byName = new Map<string, AppCatalogEntry>()
  for (const e of all) if (!byName.has(e.name)) byName.set(e.name, e)
  return [...byName.values()]
}
