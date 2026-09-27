import React, { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { OwnerSignIn, identityErrorMessage, ownerScope, readLoginStatus, readOwnerSession, signInOwner, signOutOwner,
  type LoginStatus, type OwnerScope, type OwnerSession } from './auth.web'
import { GatewayError, onIdentityExpired } from './transport.web'

export type BootstrapState =
  | { phase: 'checking'; status: LoginStatus | null; owner: null; error: '' }
  | { phase: 'signed_out' | 'signing_in' | 'unavailable' | 'signing_out'; status: LoginStatus | null; owner: null; error: string }
  | { phase: 'ready'; status: LoginStatus; owner: OwnerSession; scope: OwnerScope; error: '' }

export function isIdentityDenied(error: unknown): boolean {
  return error instanceof GatewayError && (error.status === 401 ||
    (error.status === 403 && error.code !== 'auth_origin_not_allowed'))
}

export type AssistantBootstrap = Readonly<{
  state: BootstrapState
  refresh: () => Promise<void>
  signIn: (username: string, password: string, totp: string) => Promise<void>
  signOut: () => Promise<void>
}>

const BootstrapContext = createContext<AssistantBootstrap | null>(null)

export function useAssistantBootstrap(): AssistantBootstrap {
  const value = useContext(BootstrapContext)
  if (!value) throw new Error('AssistantBootstrapProvider is required')
  return value
}

const initialState: BootstrapState = { phase: 'checking', status: null, owner: null, error: '' }

export function AssistantBootstrapProvider({ children, clearOwnerCache }: {
  children: ReactNode
  clearOwnerCache?: (scope: OwnerScope) => void
}) {
  const [state, setState] = useState<BootstrapState>(initialState)
  const stateRef = useRef<BootstrapState>(initialState)
  const epochRef = useRef(0)
  const cacheOwnerRef = useRef<OwnerScope | null>(null)
  const clearOwnerCacheRef = useRef(clearOwnerCache)
  clearOwnerCacheRef.current = clearOwnerCache

  const publish = useCallback((next: BootstrapState) => {
    stateRef.current = next
    setState(next)
  }, [])

  const clearOwner = useCallback(() => {
    const previousOwner = cacheOwnerRef.current
    cacheOwnerRef.current = null
    if (previousOwner) clearOwnerCacheRef.current?.(previousOwner)
  }, [])

  const adoptOwner = useCallback((owner: OwnerSession, status: LoginStatus) => {
    const scope = ownerScope(window.location.origin, owner)
    if (cacheOwnerRef.current && cacheOwnerRef.current.cacheKey !== scope.cacheKey) clearOwner()
    cacheOwnerRef.current = scope
    publish({ phase: 'ready', status, owner, scope, error: '' })
  }, [clearOwner, publish])

  const refresh = useCallback(async () => {
    const epoch = ++epochRef.current
    publish({ phase: 'checking', status: stateRef.current.status, owner: null, error: '' })
    try {
      const status = await readLoginStatus()
      if (epoch !== epochRef.current) return
      publish({ phase: 'checking', status, owner: null, error: '' })
      const owner = await readOwnerSession()
      if (epoch !== epochRef.current) return
      adoptOwner(owner, status)
    } catch (error) {
      if (epoch !== epochRef.current) return
      clearOwner()
      publish({
        phase: isIdentityDenied(error) ? 'signed_out' : 'unavailable',
        status: stateRef.current.status,
        owner: null,
        error: isIdentityDenied(error) ? '' : identityErrorMessage(error),
      })
    }
  }, [adoptOwner, clearOwner, publish])

  useEffect(() => {
    const unsubscribe = onIdentityExpired(() => {
      ++epochRef.current
      clearOwner()
      publish({ phase: 'signed_out', status: stateRef.current.status, owner: null, error: '' })
    })
    void refresh()
    return () => {
      ++epochRef.current
      unsubscribe()
      clearOwner()
    }
  }, [clearOwner, publish, refresh])

  const signIn = useCallback(async (username: string, password: string, totp: string) => {
    const epoch = ++epochRef.current
    clearOwner()
    publish({ phase: 'signing_in', status: stateRef.current.status, owner: null, error: '' })
    try {
      const owner = await signInOwner(username, password, totp)
      if (epoch !== epochRef.current) return
      const status = stateRef.current.status ?? await readLoginStatus()
      if (epoch !== epochRef.current) return
      adoptOwner(owner, status)
    } catch (error) {
      if (epoch === epochRef.current) publish({
        phase: 'signed_out', status: stateRef.current.status, owner: null,
        error: identityErrorMessage(error),
      })
      throw error
    }
  }, [adoptOwner, clearOwner, publish])

  const signOut = useCallback(async () => {
    const epoch = ++epochRef.current
    clearOwner()
    publish({ phase: 'signing_out', status: stateRef.current.status, owner: null, error: '' })
    try {
      await signOutOwner()
      if (epoch === epochRef.current) publish({ phase: 'signed_out', status: stateRef.current.status, owner: null, error: '' })
    } catch (error) {
      if (epoch === epochRef.current) publish({
        phase: 'unavailable', status: stateRef.current.status, owner: null,
        error: 'Could not confirm sign-out. Retry sign-out before using this browser for another owner.',
      })
      throw error
    }
  }, [clearOwner, publish])

  const value: AssistantBootstrap = { state, refresh, signIn, signOut }
  return <BootstrapContext.Provider value={value}>
    {state.phase === 'ready' ? children : state.phase === 'checking' || state.phase === 'signing_out' ?
      <p role="status">Checking Gideon session…</p> :
      state.phase === 'unavailable' && state.error.startsWith('Could not confirm sign-out') ?
        <section><p role="alert">{state.error}</p>
          <button type="button" onClick={() => void signOut()}>Retry sign-out</button></section> :
        <OwnerSignIn status={state.status} onSignIn={signIn} onRetry={() => void refresh()}
          busy={state.phase === 'signing_in'} error={state.error} />}
  </BootstrapContext.Provider>
}
