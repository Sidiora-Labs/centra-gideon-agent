import { AlertTriangle } from 'lucide-react'
import { escalationReasonSentence } from './escalationReasons'

interface EscalationAttempt {
  attempt?: number
  failure_class?: string
  error_signature?: string
  fix_instruction?: string
}

export interface EscalationRecord {
  kind?: unknown
  reason?: unknown
  detail?: unknown
  attempts?: unknown
}

export function isEscalationRecord(value: unknown): value is EscalationRecord {
  return !!value && typeof value === 'object' && (value as EscalationRecord).kind === 'escalation'
}

export function EscalationPanel({ escalation, error = '' }: { escalation: EscalationRecord; error?: string }) {
  const detail = typeof escalation.detail === 'string' ? escalation.detail.trim() : ''
  const attempts = Array.isArray(escalation.attempts) ? escalation.attempts as EscalationAttempt[] : []
  return (
    <section id="escalation" aria-labelledby="escalation-heading" className="rounded-lg border border-warning/30 bg-warning/5 p-m">
      <h2 id="escalation-heading" data-type="label-m" className="flex items-center gap-s text-warning">
        <AlertTriangle size={15} /> Escalation diagnosis
      </h2>
      <p data-type="body-s" className="mt-s text-on-surface">{escalationReasonSentence(escalation.reason)}</p>
      {detail && !error.includes(detail) && <p data-type="caption" className="mt-xs text-on-surface-var">Cause: {detail}</p>}
      {attempts.length > 0 && (
        <ol className="mt-m flex flex-col gap-s">
          {attempts.map((attempt, index) => (
            <li key={`${attempt.attempt ?? index}-${attempt.error_signature ?? ''}`} className="rounded-md bg-surface-high p-s">
              <div data-type="label-s" className="text-on-surface">
                Attempt {attempt.attempt ?? index + 1} · {attempt.failure_class || 'unknown failure'}
              </div>
              <div data-type="caption" className="mt-xs font-mono text-on-surface-low">
                Signature: {attempt.error_signature || 'not recorded'}
              </div>
              <div data-type="caption" className="mt-xs text-on-surface-var">
                Suggested fix: {attempt.fix_instruction || 'No suggested fix was recorded.'}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
