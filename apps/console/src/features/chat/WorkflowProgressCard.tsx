import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowUpRight, Workflow } from 'lucide-react'
import { api, ApiError } from '../../shared/data/api'
import { messageEnter } from '../../shared/theme/motion'
import { fvs } from '../../shared/theme/fontWeight'
import { Meter } from '../../shared/ui/Meter'
import { Button } from '../../shared/ui/Button'
import { foldEvent, foldSnapshot, type WorkflowViewModel } from '../workflows/workflowFold'
import { useWorkflowStream } from '../workflows/useWorkflowStream'
import { fmtElapsed, isTerminal, nodeLook, runLook } from '../workflows/workflowMeta'
import { TextLink } from '../../shared/ui/TextLink'
import { escalationReasonSentence } from '../workflows/escalationReasons'
import { isEscalationRecord } from '../workflows/EscalationPanel'

const WORKFLOW_TOOLS = new Set(['workflow_start', 'workflow_status', 'workflow_observe'])

export interface WorkflowRunRef { runId: string; created: boolean }

export function workflowRefFromTool(
  toolName: string | undefined,
  output: string | undefined,
): WorkflowRunRef | null {
  if (!toolName || !WORKFLOW_TOOLS.has(toolName) || !output) return null
  const m = output.match(/"run_id"\s*:\s*"([0-9a-f]{6,})"/i)
  if (!m) return null
  return { runId: m[1], created: toolName === 'workflow_start' }
}

export function WorkflowProgressCard({ refObj }: { refObj: WorkflowRunRef }) {
  const [vm, setVm] = useState<WorkflowViewModel | null>(null)
  const [gone, setGone] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const latest = useRef(0)

  const load = useCallback(async () => {
    const stamp = ++latest.current
    try {
      const snap = await api.workflowRun(refObj.runId)
      if (stamp === latest.current) { setVm(foldSnapshot(snap)); setLoadFailed(false) }
    } catch (e) {
      if (stamp !== latest.current) return
      if (e instanceof ApiError && e.status === 404) setGone(true)
      else setLoadFailed(true)
    }
  }, [refObj.runId])

  useEffect(() => { load() }, [load])

  const live = !!vm && vm.live
  useWorkflowStream(refObj.runId, live, {
    onSnapshot: (snap) => { latest.current++; setVm(foldSnapshot(snap)) },
    onLifecycle: (event, data) => setVm((prev) => (prev ? foldEvent(prev, event, data) : prev)),
  })

  useEffect(() => {
    if (!live) return
    const t = window.setInterval(load, 15_000)
    return () => window.clearInterval(t)
  }, [live, load])

  if (gone) return null

  if (!vm && loadFailed) {
    return (
      <motion.div {...messageEnter} className="my-s flex items-center gap-s rounded-xl border border-outline-variant p-m">
        <Workflow size={15} className="shrink-0 text-on-surface-low" />
        <span data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface-var">Couldn't load this workflow run</span>
        <Button variant="ghost-accent" size="xs" onClick={() => load()}>Try again</Button>
      </motion.div>
    )
  }

  const look = vm ? runLook(vm.status) : null
  const StatusIcon = look?.icon
  const pct = vm ? Math.round(vm.progress * 100) : 0

  return (
    <motion.div
      {...messageEnter}
      className="my-s flex flex-col gap-s rounded-xl border border-outline-variant p-m"
    >
      <div className="flex min-w-0 items-center gap-s">
        <Workflow size={15} className="shrink-0 text-on-surface-low" />
        <span data-type="label-s" className="min-w-0 flex-1 truncate text-on-surface" style={fvs(500)}>
          {vm?.workflow || 'Workflow'}
        </span>
        {look && StatusIcon && (
          <span data-type="caption" className={`inline-flex shrink-0 items-center gap-xs ${look.tone}`}>
            <StatusIcon size={12} className={look.spin ? 'animate-spin' : ''} /> {look.label}
          </span>
        )}
        <TextLink href={`#/workflows/runs/${refObj.runId}`} size="xs" icon={ArrowUpRight} iconPosition="trailing" iconSize={12}
          className="shrink-0 transition-colors" title="Open the run">
          Open
        </TextLink>
      </div>

      {vm && vm.totalCount > 0 && (
        <div className="flex items-center gap-s">
          <Meter size="thin" className="flex-1" pct={pct}
            label={`${vm.workflow || 'Workflow'} progress: ${vm.doneCount} of ${vm.totalCount} steps done`} />
          <span data-type="caption" className="shrink-0 text-on-surface-low tabular-nums">
            {vm.doneCount}/{vm.totalCount}
          </span>
        </div>
      )}

      {
}
      {vm?.needsInput && (
        <p data-type="caption" className="text-warning">
          {typeof vm.attention?.prompt === 'string' ? String(vm.attention.prompt) : 'Waiting on you'}
        </p>
      )}

      {vm && isEscalationRecord(vm.attention) && (
        <p data-type="caption" className="text-warning">
          {escalationReasonSentence(vm.attention.reason)}{' '}
          <TextLink href={`#/workflows/runs/${refObj.runId}#escalation`} size="xs">View diagnosis</TextLink>
        </p>
      )}

      {vm?.error && <p role="alert" data-type="caption" className="text-danger">{vm.error}</p>}

      {
}
      {vm && !isTerminal(vm.status) && (() => {
        const active = vm.nodes.find((n) => n.state === 'running')
          ?? vm.nodes.find((n) => n.state === 'waiting')
        if (!active) return null
        const nl = nodeLook(active.state)
        const NIcon = nl.icon
        return (
          <TextLink
            href={`#/workflows/runs/${encodeURIComponent(refObj.runId)}?node=${encodeURIComponent(active.node_id || active.instance_path)}`}
            size="xs"
            className="flex min-w-0 items-center gap-s text-on-surface-low"
            title="Inspect the active node"
          >
            <NIcon size={12} className={`shrink-0 ${nl.tone}${nl.spin ? ' animate-spin' : ''}`} />
            <span className="min-w-0 flex-1 truncate">{active.node_id || active.instance_path}</span>
          </TextLink>
        )
      })()}

      {vm && (vm.tokens > 0 || vm.elapsedSecs > 0) && (
        <div data-type="caption" className="flex items-center gap-m text-on-surface-low">
          {vm.elapsedSecs > 0 && <span className="tabular-nums">{fmtElapsed(vm.elapsedSecs)}</span>}
          {vm.tokens > 0 && <span className="tabular-nums">{vm.tokens.toLocaleString()} tokens</span>}
        </div>
      )}
    </motion.div>
  )
}
