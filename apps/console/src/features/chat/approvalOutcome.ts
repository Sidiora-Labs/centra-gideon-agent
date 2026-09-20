import { Check, Ban, HelpCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type ApprovalResolution = 'approved' | 'rejected' | 'trust' | 'trust_agent' | 'trust_agent_session' | 'trust_reads' | 'yolo'

export interface ApprovalOutcome {
  label: string
  icon: LucideIcon
  tone: string
}

const OUTCOMES: Record<ApprovalResolution, ApprovalOutcome> = {
  approved: { label: 'approved', icon: Check, tone: 'var(--color-ok)' },
  trust: { label: 'auto-approved (trusted for this chat)', icon: Check, tone: 'var(--color-ok)' },
  trust_agent: { label: 'auto-approved (saved for this agent)', icon: Check, tone: 'var(--color-ok)' },
  trust_agent_session: { label: 'auto-approved (agent grant limited to this chat)', icon: Check, tone: 'var(--color-ok)' },
  trust_reads: { label: 'auto-approved (reads trusted for this chat)', icon: Check, tone: 'var(--color-ok)' },
  yolo: { label: 'auto-approved (YOLO — everywhere)', icon: Check, tone: 'var(--color-ok)' },
  rejected: { label: 'denied', icon: Ban, tone: 'var(--color-on-surface-low)' },
}

export function approvalOutcome(resolved: string): ApprovalOutcome {
  return OUTCOMES[resolved as ApprovalResolution] ?? {
    label: `resolved: ${resolved.slice(0, 24)}`,
    icon: HelpCircle,
    tone: 'var(--color-on-surface-low)',
  }
}
