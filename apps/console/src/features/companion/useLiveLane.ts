import { useCallback, useEffect, useRef } from 'react'
import { invalidateKeys } from '../../shared/data/data'
import { useChatSocket, type WsMessage } from '../../shared/data/useChatSocket'

export const LANE_BURST_MS = 150
export function refreshKinds(message: WsMessage): string[] {
  if (message.type !== 'refresh') return []
  const kinds = message.data?.kinds
  return Array.isArray(kinds) ? kinds.filter((kind): kind is string => typeof kind === 'string')
    : typeof kinds === 'string' ? kinds.split(',') : []
}
const approval = (message: WsMessage) => message.type === 'approval' || message.type === 'approval_resolved'
export const FOLLOWS = {
  approvals: approval,
  loops: (message: WsMessage) => message.type === 'workflow_runs' || refreshKinds(message).some(kind => kind === 'loops' || kind === 'workflow_runs'),
  tasks: (message: WsMessage) => refreshKinds(message).includes('tasks'),
  inbox: (message: WsMessage) => message.type.startsWith('inbox') || approval(message),
  notifications: (message: WsMessage) => message.type.startsWith('notification'),
} as const

/** Invalidation starts a fresh read and fences an older in-flight response. */
export function useLiveLane(key: string, follows: (message: WsMessage) => boolean): void {
  const timer = useRef<number | undefined>(undefined)
  const reread = useCallback(() => {
    if (timer.current !== undefined) window.clearTimeout(timer.current)
    timer.current = undefined
    invalidateKeys(key)
  }, [key])
  const soon = useCallback(() => {
    if (timer.current !== undefined) return
    timer.current = window.setTimeout(reread, LANE_BURST_MS)
  }, [reread])
  useChatSocket(message => { if (follows(message)) soon() }, soon, connected => { if (connected) soon() })
  useEffect(() => {
    const shown = () => { if (!document.hidden) soon() }
    document.addEventListener('visibilitychange', shown)
    window.addEventListener('focus', shown)
    return () => {
      document.removeEventListener('visibilitychange', shown)
      window.removeEventListener('focus', shown)
      if (timer.current !== undefined) window.clearTimeout(timer.current)
      timer.current = undefined
    }
  }, [soon])
}
