import { useRef, useState } from 'react'
import type { PendingApproval } from '../../../../console/src/shared/data/api'
import type { OwnerScope } from '../../shared/auth.web'
import { createShellRoute, type ShellRoute } from '../../shared/shell/shellRoutes'
import type { ShellPalette } from '../../shared/shell/shellTheme.web'
import { GatewayError } from '../../shared/transport.web'
import { decideApproval, type ApprovalDecision } from './activityActions'

function toolInputText(value: unknown): string {
  if (typeof value === 'string') return value || 'No tool input was included.'
  if (value === undefined) return 'No tool input was included.'
  return JSON.stringify(value, null, 2) ?? 'No tool input was included.'
}

export function activityChatContinuationRoute(sessionId: string, current: ShellRoute): ShellRoute | undefined {
  if (!sessionId.trim()) return undefined
  return createShellRoute('chat', { view: 'workspace', sessionId,
    returnTo: { destination: 'activity', record: current.record,
      placement: current.placement, sessionId: current.sessionId } })
}

export function approvalChatRoute(approval: PendingApproval, current: ShellRoute): ShellRoute | undefined {
  return activityChatContinuationRoute(approval.session, current)
}

export function ActivityAttention({ approval, scope, palette, onRefresh, onActionError, onContinue }: {
  approval: PendingApproval
  scope: OwnerScope
  palette: ShellPalette
  onRefresh: () => void
  onActionError: (message: string) => void
  onContinue?: () => void
}) {
  const [submitting, setSubmitting] = useState(false)
  const [decided, setDecided] = useState<ApprovalDecision>()
  const [error, setError] = useState<string>()
  const inFlight = useRef(false)

  const decide = async (decision: ApprovalDecision) => {
    if (inFlight.current || decided) return
    inFlight.current = true
    setSubmitting(true)
    setError(undefined)
    onActionError('')
    try {
      await decideApproval(scope, approval, decision)
      setDecided(decision)
      onRefresh()
    } catch (failure) {
      const message = failure instanceof GatewayError
        ? failure.code === 'revision_conflict' || failure.status === 404
          ? 'This request changed or is no longer pending. The current native request has been refreshed for review.'
        : failure.message
        : failure instanceof Error ? failure.message : 'Gideon could not record this decision.'
      setError(message)
      onActionError(message)
      onRefresh()
    } finally {
      inFlight.current = false
      setSubmitting(false)
    }
  }

  return <section aria-label="Approval decision" data-approval-id={approval.id}
    data-approval-revision={approval.revision}
    style={{ marginTop: 20, border: `1px solid ${palette.line}`, borderRadius: 14,
      padding: 16, background: palette.secondary, display: 'grid', gap: 12 }}>
    <h3 style={{ color: palette.text, margin: 0, fontSize: 16 }}>Review this exact request</h3>
    <dl style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(230px, 100%), 1fr))',
      gap: 10, margin: 0 }}>
      <div><dt style={{ color: palette.muted, fontSize: 12 }}>Native approval ID</dt>
        <dd style={{ margin: '4px 0 0', overflowWrap: 'anywhere' }}><code>{approval.id}</code></dd></div>
      <div><dt style={{ color: palette.muted, fontSize: 12 }}>Native revision</dt>
        <dd style={{ margin: '4px 0 0', overflowWrap: 'anywhere' }}><code>{approval.revision}</code></dd></div>
      <div><dt style={{ color: palette.muted, fontSize: 12 }}>Requested tool</dt>
        <dd style={{ margin: '4px 0 0', overflowWrap: 'anywhere' }}>{approval.tool}</dd></div>
      <div><dt style={{ color: palette.muted, fontSize: 12 }}>Source and session</dt>
        <dd style={{ margin: '4px 0 0', overflowWrap: 'anywhere' }}>{approval.source} · {approval.session || 'No session'}</dd></div>
      <div><dt style={{ color: palette.muted, fontSize: 12 }}>Purpose</dt>
        <dd style={{ margin: '4px 0 0', overflowWrap: 'anywhere' }}>{approval.tool_purpose || 'No purpose supplied'}</dd></div>
      <div><dt style={{ color: palette.muted, fontSize: 12 }}>Requested at</dt>
        <dd style={{ margin: '4px 0 0' }}>{new Date(approval.ts * 1000).toLocaleString()}</dd></div>
      <div style={{ gridColumn: '1 / -1', minWidth: 0 }}><dt style={{ color: palette.muted, fontSize: 12 }}>Exact tool input</dt>
        <dd style={{ margin: '4px 0 0', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
          <code>{toolInputText(approval.tool_input)}</code></dd></div>
    </dl>
    <p style={{ margin: 0, color: palette.muted }}>Allowed decisions: approve this request or reject this request.</p>
    {error && <p role="alert" style={{ margin: 0, color: palette.danger }}>{error}</p>}
    {decided && <p role="status" style={{ margin: 0, color: palette.text }}>
      {decided === 'approve' ? 'Approval recorded.' : 'Rejection recorded.'} Refreshing native Activity records.
    </p>}
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      <button type="button" disabled={submitting || Boolean(decided)} onClick={() => void decide('approve')}
        style={{ minHeight: 44, border: 0, borderRadius: 10, padding: '8px 14px',
          background: palette.blueDark, color: '#fff', font: 'inherit', cursor: 'pointer' }}>Approve request</button>
      <button type="button" disabled={submitting || Boolean(decided)} onClick={() => void decide('reject')}
        style={{ minHeight: 44, border: `1px solid ${palette.line}`, borderRadius: 10, padding: '8px 14px',
          background: palette.card, color: palette.text, font: 'inherit', cursor: 'pointer' }}>Reject request</button>
      {onContinue && <button type="button" onClick={onContinue} style={{ minHeight: 44,
        border: `1px solid ${palette.line}`, borderRadius: 10, padding: '8px 14px',
        background: palette.card, color: palette.text, font: 'inherit', cursor: 'pointer' }}>Continue in Chat</button>}
    </div>
  </section>
}
