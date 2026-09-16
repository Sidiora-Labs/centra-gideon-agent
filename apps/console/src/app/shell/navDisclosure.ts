import { useSyncExternalStore } from 'react'

export type NavMode = 'starter' | 'expert'
export interface NavDisclosure { mode: NavMode; pinned: string[] }
export const STARTER_NAV_IDS: readonly string[] = ['dashboard', 'chat', 'inbox', 'apps', 'settings']
const starter = new Set(STARTER_NAV_IDS)
const KEY = 'nav-disclosure', EVENT = 'ne:nav-disclosure'
const absent = (): NavDisclosure => ({ mode: 'expert', pinned: [] })
let volatile: NavDisclosure | undefined
let lastSerialized: string | undefined
let lastSnapshot = absent()
export function readNavDisclosure(): NavDisclosure {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return absent()
    const value = JSON.parse(raw) as Partial<NavDisclosure> | null
    return { mode: value?.mode === 'expert' ? 'expert' : 'starter', pinned: Array.isArray(value?.pinned) ? value.pinned.filter((id): id is string => typeof id === 'string') : [] }
  } catch { return volatile ?? absent() }
}
function snapshot() {
  const state = readNavDisclosure(), serialized = JSON.stringify(state)
  if (serialized !== lastSerialized) { lastSerialized = serialized; lastSnapshot = state }
  return lastSnapshot
}
function update(change: (state: NavDisclosure) => NavDisclosure) {
  const next = change(readNavDisclosure())
  try { localStorage.setItem(KEY, JSON.stringify(next)); volatile = undefined } catch { volatile = next }
  window.dispatchEvent(new CustomEvent(EVENT))
}
export function setNavMode(mode: NavMode): void { update((state) => ({ ...state, mode })) }
export function pinNavSurface(id: string): void {
  if (!readNavDisclosure().pinned.includes(id)) update((state) => ({ ...state, pinned: [...state.pinned, id] }))
}
export function onNavDisclosureChange(callback: () => void): () => void {
  const storage = (event: StorageEvent) => { if (event.key === KEY) callback() }
  window.addEventListener(EVENT, callback)
  window.addEventListener('storage', storage)
  return () => { window.removeEventListener(EVENT, callback); window.removeEventListener('storage', storage) }
}
export function isDisclosed(id: string, mode: NavMode, pinned: readonly string[]): boolean {
  return mode === 'expert' || id.startsWith('app/') || starter.has(id) || pinned.includes(id)
}
export function undisclosedCount(ids: readonly string[], pinned: readonly string[]): number {
  const visible = new Set([...STARTER_NAV_IDS, ...pinned])
  return ids.reduce((count, id) => count + Number(!id.startsWith('app/') && !visible.has(id)), 0)
}
export function useNavDisclosure(): NavDisclosure & { setMode: (mode: NavMode) => void; pin: (id: string) => void } {
  return { ...useSyncExternalStore(onNavDisclosureChange, snapshot, snapshot), setMode: setNavMode, pin: pinNavSurface }
}
