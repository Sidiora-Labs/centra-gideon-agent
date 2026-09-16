import { useCallback, useEffect, useMemo, useReducer, useRef, type KeyboardEvent, type PointerEvent, type SetStateAction } from 'react'
import { fitPanel, initialPanelSize, keyboardSize, panelAxes, PointerResizeSession, readPanelStorage, SettledPanelSize, writePanelStorage, type PanelSide } from './resizeController'

interface PanelOptions {
  def: number
  min: number
  max: number | (() => number)
  side: PanelSide
  collapsible?: boolean
  storageKey?: string
  edgePeek?: number
}
interface PanelState { width: number; collapsed: boolean }
type PanelAction = { type: 'size'; value: number } | { type: 'collapse'; value: SetStateAction<boolean> }
function panelReducer(state: PanelState, action: PanelAction): PanelState {
  if (action.type === 'size') return action.value === state.width ? state : { ...state, width: action.value }
  const collapsed = typeof action.value === 'function' ? action.value(state.collapsed) : action.value
  return collapsed === state.collapsed ? state : { ...state, collapsed }
}

export function useResizablePanel(key: string, opts: PanelOptions) {
  const storageKey = opts.storageKey ?? `${key}-w`
  const settings = useRef(opts)
  settings.current = opts
  const bounds = useCallback(() => {
    const { min, max } = settings.current
    return { min, max: typeof max === 'function' ? max() : max }
  }, [])
  const [state, dispatch] = useReducer(panelReducer, undefined, () => ({
    width: initialPanelSize(storageKey, opts.def, bounds()),
    collapsed: !!opts.collapsible && readPanelStorage(`${key}-collapsed`) === '1',
  }))
  const [viewport, refreshViewport] = useReducer(() => ({ width: window.innerWidth, height: window.innerHeight }),
    { width: window.innerWidth, height: window.innerHeight })
  const liveWidth = useRef(state.width)
  liveWidth.current = state.width
  const drag = useRef<PointerResizeSession | null>(null)
  const persistence = useMemo(() => new SettledPanelSize(storageKey), [storageKey])

  useEffect(() => {
    window.addEventListener('resize', refreshViewport)
    return () => window.removeEventListener('resize', refreshViewport)
  }, [])
  useEffect(() => {
    persistence.schedule(state.width)
  }, [persistence, state.width])
  useEffect(() => () => persistence.flush(), [persistence])
  useEffect(() => {
    if (opts.collapsible) writePanelStorage(`${key}-collapsed`, state.collapsed ? '1' : '0')
  }, [key, opts.collapsible, state.collapsed])
  useEffect(() => () => drag.current?.stop(), [])

  const updateSize = useCallback((value: number) => {
    liveWidth.current = value
    persistence.schedule(value)
    dispatch({ type: 'size', value })
  }, [persistence])
  const onHandleDown = useCallback((event: PointerEvent) => {
    event.preventDefault()
    drag.current?.stop()
    drag.current = new PointerResizeSession(event.currentTarget as HTMLElement, event.pointerId, event,
      settings.current.side, liveWidth.current, bounds, updateSize)
  }, [bounds, updateSize])
  const onHandleKey = useCallback((event: KeyboardEvent) => {
    const next = keyboardSize(event.key, event.shiftKey, liveWidth.current, settings.current.side, bounds())
    if (next === null) return
    event.preventDefault()
    updateSize(next)
  }, [bounds, updateSize])
  const setCollapsed = useCallback((value: SetStateAction<boolean>) => dispatch({ type: 'collapse', value }), [])
  const axis = panelAxes[opts.side].dimension
  const extent = axis === 'innerWidth' ? viewport.width : viewport.height
  const { width, collapsed } = state
  const fitWidth = fitPanel(width, extent, opts.edgePeek ?? 32)
  const { min, max } = bounds()
  return { width, fitWidth, collapsed, setCollapsed, onHandleDown, onHandleKey, min, max }
}
