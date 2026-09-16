import { blastRadiusLine, deriveBlastRadius, type ApprovalRisk } from '../../features/chat/approvalMeta'

export interface ApprovalToastInput { who: string; tool: string; session: string; risk?: ApprovalRisk }
export function approvalToastMessage(input: ApprovalToastInput): string {
  const scope = blastRadiusLine(deriveBlastRadius({ tool: input.tool, risk: input.risk }))
  const subject = [input.tool, ...(scope ? [`(${scope})`] : [])].join(' ')
  return [input.who, 'needs approval to run', subject, '— open', input.session, 'to respond.'].join(' ')
}
