
const KEY = 'nav-apps'
const EVENT = 'ne:nav-apps'

export function getNavApps(): string[] {
  try {
    const raw = localStorage.getItem(KEY)
    const arr = raw ? JSON.parse(raw) : []
    return Array.isArray(arr) ? arr.filter((x) => typeof x === 'string') : []
  } catch { return [] }
}

export function isInNav(name: string): boolean {
  return getNavApps().includes(name)
}

export function setInNav(name: string, on: boolean): void {
  const cur = new Set(getNavApps())
  if (on) cur.add(name); else cur.delete(name)
  localStorage.setItem(KEY, JSON.stringify([...cur]))
  window.dispatchEvent(new CustomEvent(EVENT))
}

export function onNavAppsChange(cb: () => void): () => void {
  window.addEventListener(EVENT, cb)
  const onStorage = (e: StorageEvent) => { if (e.key === KEY) cb() }
  window.addEventListener('storage', onStorage)
  return () => { window.removeEventListener(EVENT, cb); window.removeEventListener('storage', onStorage) }
}
