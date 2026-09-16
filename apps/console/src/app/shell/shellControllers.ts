import { useCallback, useEffect, useReducer, useRef, useState, useSyncExternalStore } from 'react'
import { hasActiveTerminal, runInTerminal, runInTerminalWhenReady, subscribeTerminal } from '../../features/terminal/terminalBridge'
import type { RouteProps } from './useQueryState'

const railPreference = 'nav-collapsed'
type RailState = { collapsed: boolean; open: boolean }
type RailAction = 'toggle-desktop' | 'toggle-drawer' | 'close'
function railState(state: RailState, action: RailAction): RailState {
  if (action === 'toggle-desktop') return { ...state, collapsed: !state.collapsed }
  return { ...state, open: action === 'close' ? false : !state.open }
}

export function useShellNavigation(mobile: boolean, navigate: RouteProps['navigate']) {
  const [state, dispatch] = useReducer(railState, undefined, () => {
    let collapsed = false
    try { collapsed = localStorage.getItem(railPreference) === '1' } catch { /* Optional preference. */ }
    return { collapsed, open: false }
  })
  useEffect(() => {
    try { localStorage.setItem(railPreference, state.collapsed ? '1' : '0') } catch { /* Optional preference. */ }
  }, [state.collapsed])
  useEffect(() => { if (!mobile) dispatch('close') }, [mobile])
  useEffect(() => {
    if (!state.open) return
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.stopPropagation(); dispatch('close') }
    }
    document.addEventListener('keydown', dismiss)
    return () => document.removeEventListener('keydown', dismiss)
  }, [state.open])
  return {
    collapsed: mobile ? !state.open : state.collapsed,
    open: state.open,
    close: useCallback(() => dispatch('close'), []),
    toggle: useCallback(() => dispatch(mobile ? 'toggle-drawer' : 'toggle-desktop'), [mobile]),
    select: useCallback((path: string) => { navigate(path); if (mobile) dispatch('close') }, [navigate, mobile]),
  }
}

export function chatLaunchPath(detail: Record<string, unknown>): string {
  const query = new URLSearchParams()
  for (const [key, parameter] of [['prompt', 'seed'], ['agent', 'agent']]) {
    if (typeof detail[key] === 'string' && detail[key]) query.set(parameter, detail[key])
  }
  const path = typeof detail.session === 'string' && detail.session ? `chat/${encodeURIComponent(detail.session)}` : 'chat/new'
  return query.size ? `${path}?${query}` : path
}

function updateBadge(badges: Record<string, number>, detail: Record<string, unknown>): Record<string, number> {
  if (typeof detail.app !== 'string' || !detail.app) return badges
  const next = { ...badges }
  if (typeof detail.count === 'number' && detail.count !== 0) next[detail.app] = detail.count
  else delete next[detail.app]
  return next
}

export function useApplicationEvents(navigate: RouteProps['navigate']): Record<string, number> {
  const [badges, dispatch] = useReducer(updateBadge, {})
  const latest = useRef(navigate)
  latest.current = navigate
  useEffect(() => {
    const subscriptions: Record<string, (detail: Record<string, unknown>) => void> = {
      'ne:launch-chat': (detail) => latest.current(chatLaunchPath(detail)),
      'ne:nav-badge': dispatch,
    }
    const removals = Object.entries(subscriptions).map(([event, receive]) => {
      const listener = (message: Event) => receive((message as CustomEvent).detail ?? {})
      window.addEventListener(event, listener)
      return () => window.removeEventListener(event, listener)
    })
    return () => { for (const remove of removals) remove() }
  }, [])
  return badges
}

class TerminalShell {
  private open = false
  private pending: string | undefined
  private listeners = new Set<() => void>()
  private cleanup: (() => void) | undefined

  snapshot = () => this.open
  setOpen = (open: boolean) => {
    this.open = open
    for (const listener of [...this.listeners]) listener()
  }
  toggle = () => this.setOpen(!this.open)
  close = () => this.setOpen(false)

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    if (!this.cleanup) {
      const keyboard = (event: KeyboardEvent) => {
        if (event.key === '`' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); this.toggle() }
      }
      const command = (event: Event) => {
        const text: unknown = (event as CustomEvent).detail?.command
        if (typeof text !== 'string' || !text || (hasActiveTerminal() && runInTerminal(text))) return
        this.pending = text
        this.setOpen(true)
      }
      const removeTerminal = subscribeTerminal(() => {
        if (!this.pending || !hasActiveTerminal()) return
        const text = this.pending
        this.pending = undefined
        runInTerminalWhenReady(text)
      })
      window.addEventListener('keydown', keyboard)
      window.addEventListener('ne:run-in-terminal', command)
      this.cleanup = () => {
        removeTerminal()
        window.removeEventListener('keydown', keyboard)
        window.removeEventListener('ne:run-in-terminal', command)
      }
    }
    return () => {
      this.listeners.delete(listener)
      if (!this.listeners.size) { this.cleanup?.(); this.cleanup = undefined }
    }
  }
}

export function useTerminalShell() {
  const [controller] = useState(() => new TerminalShell())
  const open = useSyncExternalStore(controller.subscribe, controller.snapshot, controller.snapshot)
  return { open, toggle: controller.toggle, close: controller.close }
}
