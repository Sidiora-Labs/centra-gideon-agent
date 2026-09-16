import { useState } from 'react'
import { Check, TriangleAlert, X } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Checkbox, Field, NumberField, Select, TextArea, TextInput } from '../../shared/ui/forms'
import type { WorkflowContinuation } from '../../shared/data/api'
import { findGenUiBlock, widgetlessText } from '../../shared/ui/widget/blocks'
import { GenUiWidget } from '../../shared/ui/genui/GenUiWidget'
import { GenUiHostCtx } from '../../shared/ui/genui/actions'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function WorkflowAsk({ continuation, runId, busy, onAnswer }: {
  continuation: WorkflowContinuation
  runId: string
  busy: boolean
  onAnswer: (c: WorkflowContinuation, value: unknown, alwaysAllow: boolean) => void | Promise<void>
}) {
  const { ask, handoff, expired } = continuation
  const kind = ask.kind || 'approval'
  const [text, setText] = useState('')
  const [choice, setChoice] = useState(ask.choices?.[0] ?? '')
  const [form, setForm] = useState<Record<string, unknown>>(() => {
    const seed: Record<string, unknown> = {}
    for (const f of ask.fields ?? []) if (f.type === 'boolean') seed[f.name] = false
    return seed
  })
  const [alwaysAllow, setAlwaysAllow] = useState(false)
  const setField = (name: string, value: unknown) => setForm((p) => ({ ...p, [name]: value }))

  if (expired) {
    return (
      <div className="flex flex-col gap-s rounded-xl border border-outline-variant p-l">
        <span data-type="body-s" className="inline-flex items-center gap-s text-on-surface-low">
          <TriangleAlert size={14} /> This request expired before it was answered.
        </span>
        <p data-type="caption" className="text-on-surface-low">
          Re-run the workflow from <span className="font-mono">{continuation.node_id}</span> to ask again.
        </p>
      </div>
    )
  }

  const gateWidget = findGenUiBlock(ask.prompt || '')
  const promptText = widgetlessText(ask.prompt || '')
  const gateHost = {
    producer: { kind: 'workflow-gate' as const, runId, token: continuation.resume_token },
  }

  const hasContext = !!(handoff.checks_run?.length || handoff.outstanding?.length || handoff.risks?.length)

  return (
    <div className="flex flex-col gap-m rounded-xl border border-outline-variant p-l">
      {gateWidget ? (
        <>
          {promptText && <p data-type="body-m" className="text-on-surface">{promptText}</p>}
          <GenUiHostCtx.Provider value={gateHost}>
            <GenUiWidget content={gateWidget.html} title={gateWidget.title} />
          </GenUiHostCtx.Provider>
        </>
      ) : (
        <p data-type="body-m" className="text-on-surface">{ask.prompt || 'This run needs your input.'}</p>
      )}

      {
}
      {hasContext && (
        <div data-type="caption" className="flex flex-col gap-xs text-on-surface-low">
          {!!handoff.checks_run?.length && <span>Already done: {handoff.checks_run.length} step{handoff.checks_run.length === 1 ? '' : 's'}</span>}
          {!!handoff.outstanding?.length && <span>Still to do: {handoff.outstanding.length} step{handoff.outstanding.length === 1 ? '' : 's'}</span>}
          {handoff.risks?.map((r) => <span key={r} className="text-warning">Risk: {r}</span>)}
        </div>
      )}

      {kind === 'choice' && (
        <Field label="Choose one">
          <Select
            value={choice}
            onChange={setChoice}
            options={(ask.choices ?? []).map((c) => ({ value: c, label: c }))}
          />
        </Field>
      )}

      {kind === 'text' && (
        <Field label="Your answer">
          <TextArea value={text} onChange={setText} rows={3} ariaLabel="Your answer" />
        </Field>
      )}

      {kind === 'form' && (
        <div className="flex flex-col gap-s">
          {(ask.fields ?? []).map((f) => (
            <Field key={f.name} label={f.label || f.name}>
              {f.type === 'boolean' ? (
                <Checkbox
                  checked={!!form[f.name]}
                  onChange={(v) => setField(f.name, v)}
                  ariaLabel={f.label || f.name}
                />
              ) : f.type === 'choice' ? (
                <Select
                  value={String(form[f.name] ?? f.choices?.[0] ?? '')}
                  onChange={(v) => setField(f.name, v)}
                  options={(f.choices ?? []).map((c) => ({ value: c, label: c }))}
                />
              ) : f.type === 'number' ? (
                <NumberField
                  value={Number(form[f.name] ?? 0)}
                  onChange={(v) => setField(f.name, v)}
                  ariaLabel={f.label || f.name}
                />
              ) : (
                <TextInput
                  value={String(form[f.name] ?? '')}
                  onChange={(v) => setField(f.name, v)}
                  ariaLabel={f.label || f.name}
                />
              )}
            </Field>
          ))}
        </div>
      )}

      <label data-type="caption" className="inline-flex items-center gap-s text-on-surface-low">
        <Checkbox checked={alwaysAllow} onChange={setAlwaysAllow} ariaLabel="Don't ask again for this step in this run" />
        Don&apos;t ask again for this step in this run
      </label>

      <div className="flex items-center gap-s">
        {kind === 'approval' ? (
          <>
            <Button onClick={() => onAnswer(continuation, true, alwaysAllow)} disabled={busy} disabledReason={BUSY_REASON}>
              <Check size={14} /> Approve
            </Button>
            {
}
            <QuietButton onClick={() => onAnswer(continuation, false, alwaysAllow)} title="Deny this step">
              <X size={13} /> Deny
            </QuietButton>
          </>
        ) : (
          <Button
            onClick={() => onAnswer(
              continuation,
              kind === 'choice' ? choice : kind === 'text' ? text : form,
              alwaysAllow,
            )}
            disabled={busy || (kind === 'text' && !text.trim())}
            disabledReason={kind === 'text' && !text.trim() ? 'Type an answer first' : BUSY_REASON}
          >
            <Check size={14} /> Submit
          </Button>
        )}
      </div>
    </div>
  )
}
