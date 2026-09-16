import type { WsMessage } from '../../shared/data/useChatSocket'
import { approvalToastMessage, type ApprovalToastInput } from './approvalToast'

export class ApprovalNotifications {
  private history = new Set<string>()
  receive(message: WsMessage, activeSession: string): string | undefined {
    if (message.type !== 'approval') return
    const data = message.data ?? {}
    const session = String(data.session ?? ''), id = String(data.id ?? '')
    if (!session || !id || session === activeSession || this.history.has(id)) return
    this.history.add(id)
    if (this.history.size > 200) this.history = new Set([...this.history].slice(-100))
    const source = String(data.source ?? '')
    const who = source === 'subagent' ? 'A subagent' : source ? 'A background task' : 'Another chat session'
    return approvalToastMessage({ who, session, tool: String(data.tool ?? 'a tool'), risk: (data.risk ? String(data.risk) : undefined) as ApprovalToastInput['risk'] })
  }
}
