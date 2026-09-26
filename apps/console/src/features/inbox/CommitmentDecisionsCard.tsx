import { useState } from 'react'
import { api, type CommitmentDecision } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { confirm, promptForm, promptInput } from '../../shared/ui/dialog'
import { notify } from '../../app/shell/appSdk'
import { rankWorkflowDefinitions } from '../workflows/templateSuggest'
import { coerceInputs, inputFields, startsWithoutInput } from '../workflows/templateStart'

export function CommitmentDecisionsCard({ decisions, onRefresh }: {
  decisions: CommitmentDecision[]; onRefresh: () => void
}) {
  const [busy, setBusy] = useState('')
  if (!decisions.length) return null

  async function dismiss(row: CommitmentDecision) {
    setBusy(row.topic)
    try {
      await api.proactiveCommitmentReply(row.topic, 'dismiss')
      onRefresh()
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Could not dismiss this follow-up', 'error')
    } finally { setBusy('') }
  }

  async function approve(row: CommitmentDecision) {
    const defs = (await api.workflowDefs()).defs
    const choices = rankWorkflowDefinitions(row.text, defs, {}).slice(0, 4)
    const name = await promptInput({
      title: 'Approve background work',
      body: `Follow-up: ${row.text}\n\nSaved workflows: ${choices.map((choice) => choice.definition.name).join(', ') || 'none'}. Choose a saved workflow; it will create one inspectable run.`,
      label: 'Saved workflow', initial: choices[0]?.definition.name || '',
      confirmLabel: 'Review workflow',
    })
    if (!name) return
    const def = (await api.workflowDef(name)).definition
    let inputs: Record<string, unknown> = {}
    if (!startsWithoutInput(def.inputs)) {
      const answers = await promptForm({ title: `Inputs for ${name}`, fields: inputFields(def.inputs), confirmLabel: 'Review launch' })
      if (answers === null) return
      inputs = coerceInputs(answers, def.inputs)
    }
    if (!(await confirm({ title: `Start ${name} in the background?`, body: `${def.description || name}\n\nFor: ${row.text}`, confirmLabel: 'Approve and run' }))) return
    setBusy(row.topic)
    try {
      const result = await api.proactiveCommitmentReply(row.topic, 'approve_background', name, inputs)
      if (result.run_id) onRefresh()
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Could not start background work', 'error')
    } finally { setBusy('') }
  }

  return <section aria-label="Proactive decisions" className="mt-l rounded-xl border border-outline/30 bg-surface-high p-m">
    <h3 data-type="title-s">Follow-up decisions</h3>
    <ul className="mt-s flex flex-col gap-s">
      {decisions.map((row) => <li key={row.topic} className="rounded-lg border border-outline/20 p-m">
        <p data-type="body-s">{row.text}</p>
        <p data-type="caption" className="mt-xs text-on-surface-low">
          {row.action}: {row.reason} · policy {row.policy} · destination {row.destination || 'none'}
          {row.delivered_at ? ` · delivered ${row.delivered_at.slice(0, 16)}` : ''}
          {row.error ? ` · ${row.error}` : ''}
          {row.dismissed_until ? ` · dismissed until ${row.dismissed_until.slice(0, 16)}` : ''}
        </p>
        {row.run_id ? <a className="text-primary" href={`#/workflows/runs/${encodeURIComponent(row.run_id)}`}>Open background run</a>
          : !row.dismissed_until && <div className="mt-s flex gap-s">
            <Button size="xs" variant="secondary" loading={busy === row.topic} onClick={() => void approve(row).catch((error) => notify(error instanceof Error ? error.message : 'Could not review background work', 'error'))}>Approve background work</Button>
            <Button size="xs" variant="secondary" loading={busy === row.topic} onClick={() => void dismiss(row)}>Dismiss for seven days</Button>
          </div>}
      </li>)}
    </ul>
  </section>
}
