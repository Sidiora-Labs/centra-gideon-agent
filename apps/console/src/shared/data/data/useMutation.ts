import { useCallback, useReducer, useRef } from 'react'
import { invalidateSpecs, type CacheKeySpec } from './store'

export interface MutationSpec<A extends unknown[], R> {
  run: (...args: A) => Promise<R>
  invalidates: readonly CacheKeySpec[] | ((res: R, ...args: A) => readonly CacheKeySpec[])
  onSuccess?: (res: R, ...args: A) => void
  onError?: (err: unknown, ...args: A) => void
}
export interface MutationResult<A extends unknown[], R> {
  mutate: (...args: A) => Promise<R>
  pending: boolean
  error: unknown
}

type WriteState = { active: number; error: unknown }
type WriteAction = { type: 'begin' | 'finish' | 'error'; error?: unknown }
function writeState(state: WriteState, action: WriteAction): WriteState {
  switch (action.type) {
    case 'begin': return { active: state.active + 1, error: null }
    case 'finish': return { ...state, active: Math.max(0, state.active - 1) }
    case 'error': return { ...state, error: action.error }
  }
}

export function useMutation<A extends unknown[], R>(spec: MutationSpec<A, R>): MutationResult<A, R> {
  const latest = useRef(spec)
  latest.current = spec
  const [state, dispatch] = useReducer(writeState, { active: 0, error: null })
  const mutate = useCallback(async (...args: A): Promise<R> => {
    const operation = latest.current
    dispatch({ type: 'begin' })
    try {
      const result = await operation.run(...args)
      const affected = typeof operation.invalidates === 'function'
        ? operation.invalidates(result, ...args) : operation.invalidates
      invalidateSpecs(affected)
      operation.onSuccess?.(result, ...args)
      return result
    } catch (error) {
      dispatch({ type: 'error', error })
      if (!operation.onError) throw error
      operation.onError(error, ...args)
      return undefined as R
    } finally {
      dispatch({ type: 'finish' })
    }
  }, [])
  return { mutate, pending: state.active > 0, error: state.error }
}
