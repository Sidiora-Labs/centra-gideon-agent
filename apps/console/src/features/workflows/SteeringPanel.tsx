import { useCallback, useEffect, useState } from 'react'
import { MessageSquarePlus, Send, Gavel } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { TextArea } from '../../shared/ui/forms'
import { QuietButton } from '../../shared/ui/QuietButton'
import { api, type WorkflowNodeState } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { promptForm } from '../../shared/ui/dialog'
import { nodeLabel } from './workflowMeta'
import { canSteerComment, judgeComment, steerTextFromComment } from './steeringMeta'

export function SteeringPanel({
  runId,
  projectId,
  nodes,
  onSteered,
}: {
  runId: string
  projectId?: string
  nodes: WorkflowNodeState[]
  onSteered?: () => void
}) {
  const [pending, setPending] = useState<Array<{ text: string; queued_at: string }>>([])
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)

  const refetch = useCallback(async () => {
    try {
      const res = await api.workflowSteering(runId)
      setPending(res.pending ?? [])
    } catch {
    }
  }, [runId])
  useEffect(() => { refetch() }, [refetch])

  const steer = useCallback(async (text: string) => {
    const t = text.trim()
    if (!t || busy) return
    setBusy(true)
    try {
      const res = await api.steerWorkflowRun(runId, { text: t })
      if (res.ok === false) {
        notify(res.error?.message ?? 'Could not queue that instruction.', 'error')
        return
      }
      setDraft('')
      await refetch()
      onSteered?.()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not queue that instruction.', 'error')
    } finally {
      setBusy(false)
    }
  }, [busy, runId, refetch, onSteered])

  const flagged = nodes.filter((n) => canSteerComment(n, true))

  const setJudgeGuidance = useCallback(async () => {
    if (!projectId) return
    let current = ''
    try {
      current = (await api.project(projectId)).agent_instructions_template ?? ''
    } catch {
    }
    const answers = await promptForm({
      title: 'Judge guidance for this project',
      body: 'Standing guidance for every run under this project — it reaches the worker and the judge. Applies to future cycles, not the ones already scored.',
      fields: [{
        name: 'guidance',
        label: 'Guidance',
        type: 'textarea',
        initial: current,
        placeholder: 'e.g. Prefer primary sources; reject a summary that cites none.',
      }],
      confirmLabel: 'Save guidance',
    })
    if (answers === null) return
    try {
      await api.updateProject(projectId, { agent_instructions_template: answers.guidance ?? '' })
      notify('Judge guidance saved for this project.')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not save the guidance.', 'error')
    }
  }, [projectId])

  return (
    <div className="flex flex-col gap-l">
      { }
      {flagged.length > 0 && (
        <div className="flex flex-col gap-s">
          <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">Judge comments</div>
          {flagged.map((n) => {
            const comment = judgeComment(n)
            return (
              <div key={n.instance_path} className="rounded-lg bg-surface-high px-m py-2">
                <div data-type="body-s" className="text-on-surface">{nodeLabel(n)}</div>
                <div data-type="caption" className="mt-0.5 text-on-surface-low">{comment}</div>
                <div className="mt-1.5 flex justify-end">
                  <QuietButton
                    onClick={() => steer(steerTextFromComment(nodeLabel(n), comment))}
                    title="Send this feedback to the worker — applied at the next iteration"
                  >
                    <Send size={12} /> Accept &amp; steer
                  </QuietButton>
                </div>
              </div>
            )
          })}
        </div>
      )}

      { }
      <div className="flex flex-col gap-s">
        <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">Steer this run</div>
        <TextArea
          value={draft}
          onChange={setDraft}
          rows={3}
          ariaLabel="Steering instruction"
          placeholder="Guide the next iteration — focus an angle, or answer what the run is stuck on."
        />
        <div className="flex justify-end">
          <Button size="sm" onClick={() => steer(draft)} loading={busy} loadingLabel="Queuing…" disabled={!draft.trim() || busy}
            disabledReason={!draft.trim() ? 'Write a steering note first' : undefined}>
            <MessageSquarePlus size={14} /> Queue instruction
          </Button>
        </div>
        {pending.length > 0 && (
          <div data-type="body-s" className="rounded-lg px-m py-2" style={{ background: 'color-mix(in srgb, var(--color-info) 8%, transparent)', border: '1px dashed color-mix(in srgb, var(--color-info) 30%, transparent)' }}>
            <div data-type="caption" className="flex items-center gap-1.5 text-info uppercase tracking-wide mb-1">
              <MessageSquarePlus size={12} /> queued — applies next iteration
            </div>
            {pending.map((p, i) => <p key={i} className="text-on-surface-var">{p.text}</p>)}
          </div>
        )}
      </div>

      { }
      {projectId && (
        <div className="flex flex-col gap-s">
          <div data-type="caption" className="text-on-surface-low uppercase tracking-wide">Project</div>
          <QuietButton onClick={setJudgeGuidance} title="Standing guidance for every run under this project — reaches the worker and the judge">
            <Gavel size={12} /> Judge guidance…
          </QuietButton>
        </div>
      )}
    </div>
  )
}
