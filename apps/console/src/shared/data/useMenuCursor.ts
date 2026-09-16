import { useCallback, useEffect, useReducer, useRef, type RefObject } from 'react'
import { captureFocus, returnFocus } from '../ui/focusNavigation'

interface CursorState { key: string | null; seed: number; index: number; engaged: boolean }
type CursorAction = { type: 'reset'; key: string | null; seed: number }
  | { type: 'move'; delta: number; count: number; automatic: boolean }
const clamp = (index: number, count: number) => Math.max(0, Math.min(Math.max(0, count - 1), index))
function cursorReducer(state: CursorState, action: CursorAction): CursorState {
  if (action.type === 'reset') return { key: action.key, seed: action.seed, index: action.seed, engaged: false }
  const index = clamp(state.index, action.count)
  const enterOnly = !action.automatic && !state.engaged && Math.abs(action.delta) === 1
  return { ...state, index: enterOnly ? index : clamp(index + action.delta, action.count), engaged: true }
}

export function useMenuCursor({ containerRef, count, openKey, initialIndex = 0, autoFocus = true }: {
  containerRef: RefObject<HTMLElement | null>
  count: number
  openKey: string | null
  initialIndex?: number
  autoFocus?: boolean
}) {
  const [cursor, dispatch] = useReducer(cursorReducer, { key: null, seed: initialIndex, index: initialIndex, engaged: false })
  const origin = useRef<HTMLElement | null>(null)
  if (cursor.key !== openKey || cursor.seed !== initialIndex) {
    if (openKey !== null) {
      const focused = captureFocus()
      if (focused && !containerRef.current?.contains(focused)) origin.current = focused
    }
    dispatch({ type: 'reset', key: openKey, seed: initialIndex })
  }
  const active = clamp(cursor.index, count)
  useEffect(() => {
    if (openKey === null || (!autoFocus && !cursor.engaged)) return
    const rows = containerRef.current?.querySelectorAll<HTMLElement>(
      '[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"],[role="option"]')
    rows?.[active]?.focus({ preventScroll: true })
  }, [openKey, active, cursor.engaged, count, autoFocus, containerRef])

  const move = useCallback((delta: number) => dispatch({ type: 'move', delta, count, automatic: autoFocus }), [count, autoFocus])
  const restoreFocus = useCallback(() => returnFocus(origin.current, containerRef.current, true), [containerRef])
  return { active, move, restoreFocus, tabIndexFor: (index: number) => index === active ? 0 : -1 }
}

const menuSteps = new Map([
  ['ArrowDown', 1], ['ArrowUp', -1], ['Home', -Number.MAX_SAFE_INTEGER], ['End', Number.MAX_SAFE_INTEGER],
])
export function menuCursorKeydown(event: KeyboardEvent, commands: { move: (delta: number) => void; dismiss: () => void }): boolean {
  if (event.defaultPrevented) return false
  if (event.key === 'Tab') { commands.dismiss(); return true }
  const delta = menuSteps.get(event.key)
  if (delta === undefined) return false
  event.preventDefault()
  commands.move(delta)
  return true
}
