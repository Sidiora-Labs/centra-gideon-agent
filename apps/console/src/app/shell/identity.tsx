import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from 'react'
import { api } from '../../shared/data/api'
import { identityReducer, initialIdentity } from './identityState'

interface Identity {
  name: string
  onboarded: boolean
  loaded: boolean
  setName: (name: string, handle?: string) => Promise<void>
  clearName: () => Promise<void>
}
export const DEFAULT_USER_NAME = 'Operator'
export const USERNAME_MAX_LEN = 32
export function suggestHandle(displayName: string): string {
  return displayName
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, USERNAME_MAX_LEN)
    .replace(/[-_]+$/, '')
}
const IdentityCtx = createContext<Identity>({ name: '', onboarded: false, loaded: false, setName: async () => {}, clearName: async () => {} })

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(identityReducer, initialIdentity)
  const writes = useRef(Promise.resolve())
  useEffect(() => {
    let active = true
    const load = async () => {
      let name = ''
      try { name = (await api.dashboardConfig()).user_name || '' } catch { /* Unavailable identity opens onboarding. */ }
      if (active) dispatch({ type: 'loaded', name, revision: 0 })
    }
    void load()
    return () => { active = false }
  }, [])
  const setName = useCallback((name: string, handle?: string) => {
    const normalized = name.trim()
    dispatch({ type: 'edit', name: normalized })
    writes.current = writes.current.then(async () => {
      try { await api.saveDashboardConfig({ user_name: normalized, ...(handle === undefined ? {} : { username: handle.trim().slice(0, USERNAME_MAX_LEN) }) }) } catch { /* Keep the optimistic session identity. */ }
    })
    return writes.current
  }, [])
  const clearName = useCallback(() => setName(''), [setName])
  const value = useMemo(() => ({ name: state.name, loaded: state.loaded, onboarded: state.name.trim().length > 0, setName, clearName }), [state.name, state.loaded, setName, clearName])
  return <IdentityCtx.Provider value={value}>{children}</IdentityCtx.Provider>
}
export const useIdentity = () => useContext(IdentityCtx)
export function firstNameOf(name: string): string { return name.trim().split(/\s+/)[0] || 'there' }
