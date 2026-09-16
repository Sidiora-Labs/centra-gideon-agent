import { useRef } from 'react'
import { useChatSocket } from '../../shared/data/useChatSocket'
import { playCue } from '../../shared/theme/soundCues'
import { ApprovalNotifications } from './approvalNotifications'

export function useApprovalToasts(activeSession: string) {
  const notifications = useRef<ApprovalNotifications | null>(null)
  notifications.current ??= new ApprovalNotifications()
  useChatSocket((event) => {
    const message = notifications.current!.receive(event, activeSession)
    if (!message) return
    window.dispatchEvent(new CustomEvent('ne:toast', { detail: { level: 'info', message } }))
    playCue('approval_needed')
  })
}
