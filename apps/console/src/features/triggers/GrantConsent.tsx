import { useState } from 'react'
import { api, type TriggerGrantQuestion } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { FieldError } from '../../shared/ui/forms'

export function GrantConsent({ id, question, required, satisfied, readOnly, onChanged }: {
  id: string; question?: TriggerGrantQuestion | null
  required?: boolean; satisfied?: boolean; readOnly?: boolean; onChanged: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  if (readOnly || (!question && (!required || satisfied))) return null
  async function allow() {
    const displayed = question
    if (!displayed) return
    setBusy(true); setError('')
    try { await api.grantTrigger(id, displayed); onChanged() }
    catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Could not confirm this action')
      onChanged()
    } finally { setBusy(false) }
  }
  return <div className="rounded-lg border border-outline-var px-m py-m flex flex-col gap-2">
    <div className="text-on-surface text-[0.8125rem]">{question?.sentence ?? 'The workflow could not be read. Review it before allowing this automation.'}</div>
    {question && <div><Button size="sm" loading={busy} onClick={allow}>{satisfied ? 'Use these versions' : 'Allow'}</Button></div>}
    {error && <FieldError>{error}</FieldError>}
  </div>
}
