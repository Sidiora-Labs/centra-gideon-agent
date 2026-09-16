import { useCallback, useEffect, useReducer, useRef, useSyncExternalStore } from 'react'
import { fetchKey, isStale, readEntry, subscribeKey } from './store'
import { staleAfterMsFor } from './keys'

export type QueryStatus = 'loading' | 'success' | 'error'
export interface QueryResult<T> {
  data: T | undefined
  loading: boolean
  revalidating: boolean
  stale: boolean
  error: unknown
  status: QueryStatus
  refresh: () => void
}

type ReadState = { key: string; pending: boolean; error: unknown }
type ReadAction = { key: string; type: 'start' | 'complete'; error?: unknown }
function readState(state: ReadState, action: ReadAction): ReadState {
  if (action.type === 'start') return { key: action.key, pending: true, error: state.key === action.key ? state.error : null }
  return { key: action.key, pending: false, error: action.error ?? null }
}

export function useQuery<T>(
  key: string,
  fetcher: () => Promise<T>,
  { persist = false, staleAfterMs = staleAfterMsFor(key) }: { persist?: boolean; staleAfterMs?: number } = {},
): QueryResult<T> {
  const latest = useRef(fetcher)
  latest.current = fetcher
  const subscribe = useCallback((listener: () => void) => subscribeKey(key, listener), [key])
  const snapshot = useCallback(() => readEntry<T>(key, persist), [key, persist])
  const entry = useSyncExternalStore(subscribe, snapshot, () => undefined)
  const [request, dispatch] = useReducer(readState, { key, pending: true, error: null })
  const [refreshVersion, refresh] = useReducer((value: number) => value + 1, 0)
  const epoch = entry?.epoch ?? 0

  useEffect(() => {
    let current = true
    dispatch({ type: 'start', key })
    const complete = (error?: unknown) => {
      if (current) dispatch({ type: 'complete', key, error })
    }
    void fetchKey(key, () => latest.current(), persist).then(() => complete(), complete)
    return () => { current = false }
  }, [key, epoch, refreshVersion, persist, staleAfterMs])

  const data = entry?.value
  const sameKey = request.key === key
  const pending = !sameKey || request.pending
  const error = sameKey ? request.error : null
  return {
    data,
    loading: data === undefined && pending,
    revalidating: pending,
    stale: isStale(key, entry, staleAfterMs),
    error,
    status: data !== undefined ? 'success' : error !== null ? 'error' : 'loading',
    refresh,
  }
}
