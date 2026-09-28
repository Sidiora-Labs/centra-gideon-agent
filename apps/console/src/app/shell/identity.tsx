import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode } from 'react'
import { api } from '../../shared/data/api'
import { identityReducer, initialIdentity } from './identityState'

interface Identity {
  name: string
  onboarded: boolean
  loaded: boolean
  identityError: string
  retryIdentity: () => Promise<void>
  setName: (name: string, handle?: string) => Promise<void>
  keepOrDefaultName: () => Promise<void>
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
const IDENTITY_READ_ERROR = "Gideon couldn't load your account. Check your connection and try again."
const IdentityCtx = createContext<Identity>({ name: '', onboarded: false, loaded: false, identityError: '', retryIdentity: async () => {}, setName: async () => {}, keepOrDefaultName: async () => {}, clearName: async () => {} })

export function IdentityProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(identityReducer, initialIdentity)
  const writes = useRef(Promise.resolve())
  const request = useRef(0)
  const retryIdentity = useCallback(async () => {
    const currentRequest = ++request.current
    dispatch({ type: 'loadStarted', request: currentRequest })
    try {
      const config = await api.dashboardConfig()
      if (request.current !== currentRequest) return
      dispatch({ type: 'loaded', name: config.user_name || '', revision: 0, request: currentRequest })
    } catch {
      if (request.current !== currentRequest) return
      dispatch({ type: 'loadFailed', error: IDENTITY_READ_ERROR, request: currentRequest })
    }
  }, [])
  useEffect(() => {
    void retryIdentity()
    return () => { request.current += 1 }
  }, [retryIdentity])
  const setName = useCallback((name: string, handle?: string) => {
    const normalized = name.trim()
    const write = writes.current.then(async () => {
      await api.saveDashboardConfig({ user_name: normalized, ...(handle === undefined ? {} : { username: handle.trim().slice(0, USERNAME_MAX_LEN) }) })
      const saved = await api.dashboardConfig()
      dispatch({ type: 'edit', name: saved.user_name || '' })
    })
    writes.current = write.catch(() => {})
    return write
  }, [])
  const keepOrDefaultName = useCallback(() => {
    const write = writes.current.then(async () => {
      const result = await api.keepOrDefaultDashboardName()
      if (!result.ok) throw new Error('Could not save your setup. Please try again.')
      if (!result.identity.user_name.trim() && result.identity.username.trim()) {
        throw new Error('Your saved handle is still here. Add a name to finish setup; nothing was changed.')
      }
      dispatch({ type: 'edit', name: result.identity.user_name })
    })
    writes.current = write.catch(() => {})
    return write
  }, [])
  const clearName = useCallback(() => setName(''), [setName])
  const value = useMemo(() => ({ name: state.name, loaded: state.status === 'loaded', identityError: state.error, onboarded: state.name.trim().length > 0, retryIdentity, setName, keepOrDefaultName, clearName }), [state.name, state.status, state.error, retryIdentity, setName, keepOrDefaultName, clearName])
  return <IdentityCtx.Provider value={value}>{children}</IdentityCtx.Provider>
}
export const useIdentity = () => useContext(IdentityCtx)
export function firstNameOf(name: string): string { return name.trim().split(/\s+/)[0] || 'there' }
