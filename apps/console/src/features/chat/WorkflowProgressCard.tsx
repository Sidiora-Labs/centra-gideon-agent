import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { ArrowUpRight, ChevronDown, Workflow } from 'lucide-react'
import { api, ApiError, type NodeInspect, type WorkflowBatchState, type PendingApproval } from '../../shared/data/api'
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
import { DagView } from '../tasks/DagView'
import { layoutRunDag } from '../workflows/runDag'

const WORKFLOW_TOOLS = new Set(['workflow_start', 'workflow_status', 'workflow_observe', 'subagent_run'])

export interface WorkflowRunRef { runId: string; created: boolean; batchName?: string }

export function workflowRefFromTool(
  toolName: string | undefined,
  output: string | undefined,
): WorkflowRunRef | null {
  if (!toolName || !WORKFLOW_TOOLS.has(toolName) || !output) return null
  const consentBatch = output.match(/"batch_start_consent"\s*:\s*\{\s*"name"\s*:\s*"([a-zA-Z0-9_-]+)"/)
  if (consentBatch) return { runId: '', created: false, batchName: consentBatch[1] }
  if (toolName === 'subagent_run') {
    const batch = output.match(/"batch"\s*:\s*"([a-zA-Z0-9_-]+)"/)
    if (batch) return { runId: '', created: true, batchName: batch[1] }
  }
  const m = output.match(/"run_id"\s*:\s*"([0-9a-f]{6,})"/i)
  if (!m) return null
  return { runId: m[1], created: toolName === 'workflow_start' }
}

export function WorkflowProgressCard({ refObj }: { refObj: WorkflowRunRef }) {
  return refObj.batchName ? <BatchProgressCard name={refObj.batchName} /> : <RunProgressCard refObj={refObj} />
}

function BatchProgressCard({ name }: { name: string }) {
  const [batch, setBatch] = useState<WorkflowBatchState | null>(null)
  const [question, setQuestion] = useState<PendingApproval | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try {
      const row = await api.workflowBatch(name)
      setBatch(row)
      if (row.status === 'awaiting_approval') {
        const pending = await api.approvals()
        setQuestion(pending.find((ask) => ask.id === row.approval) ?? null)
      } else setQuestion(null)
    } catch (e) { setError(e instanceof Error ? e.message : 'Could not load this batch.') }
  }, [name])
  useEffect(() => { if (batch?.run_id || batch?.status === 'not_started') return; void load(); const timer = window.setInterval(() => void load(), 3000); return () => window.clearInterval(timer) }, [load, batch?.run_id, batch?.status])
  const answer = async (action: 'approve' | 'reject') => {
    if (!question) return
    setBusy(true); setError('')
    try { await api.resolveApproval(question.id, action, question.revision); await load() }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not answer this batch.'); await load() }
    finally { setBusy(false) }
  }
  if (batch?.run_id && batch.status !== 'not_started') return <RunProgressCard refObj={{ runId: batch.run_id, created: true }} />
  return <motion.div {...messageEnter} className="my-s flex flex-col gap-s rounded-xl border border-outline-variant p-m">
    <div className="flex items-center gap-s"><Workflow size={15} /><span data-type="label-s">Batch of this conversation</span></div>
    <p data-type="body-s">{batch?.status === 'not_started' ? 'Did not start' : batch?.status === 'starting' ? 'Starting' : `Waiting for your Allow${batch ? ` · ${batch.tasks} tasks` : ''}`}</p>
    {question && <><p data-type="body-s">{question.tool_purpose}</p><pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs text-on-surface-var">{typeof question.tool_input === 'string' ? question.tool_input : JSON.stringify(question.tool_input)}</pre>
      <div className="flex gap-s"><Button size="xs" disabled={busy} onClick={() => void answer('approve')} disabledReason={busy ? 'Wait for the workflow task decision to finish' : undefined}>Allow these tasks</Button><Button variant="ghost-accent" size="xs" disabled={busy} onClick={() => void answer('reject')} disabledReason={busy ? 'Wait for the workflow task decision to finish' : undefined}>Deny</Button></div></>}
    {batch?.error && <p role="alert" data-type="caption" className="text-danger">{batch.error}</p>}
    {batch?.run_id && batch.status === 'not_started' && <TextLink href={`#/workflows/runs/${encodeURIComponent(batch.run_id)}`} size="xs">Inspect the failed start</TextLink>}
    {error && <p role="alert" data-type="caption" className="text-danger">{error}</p>}
    {batch?.status === 'awaiting_approval' && <TextLink href="#/inbox" size="xs">View the waiting approval in Inbox</TextLink>}
  </motion.div>
}

function RunProgressCard({ refObj }: { refObj: WorkflowRunRef }) {
  const [vm, setVm] = useState<WorkflowViewModel | null>(null)
  const [gone, setGone] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [graphOpen, setGraphOpen] = useState(false)
  const [selectedNode, setSelectedNode] = useState('')
  const [nodeDetail, setNodeDetail] = useState<NodeInspect | null>(null)
  const [nodeError, setNodeError] = useState('')
  const selectedNodeId = vm?.nodes.find((node) => node.instance_path === selectedNode)?.node_id || selectedNode
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

  useEffect(() => {
    if (!selectedNodeId) { setNodeDetail(null); setNodeError(''); return }
    let live = true
    setNodeDetail(null)
    setNodeError('')
    api.workflowRunNodeInspect(refObj.runId, selectedNodeId)
      .then((detail) => { if (live) setNodeDetail(detail) })
      .catch((error) => { if (live) setNodeError(error instanceof ApiError && error.status === 409
        ? 'This node is still running. Its final trace appears when it finishes.'
        : error instanceof Error ? error.message : 'Could not load node detail.') })
    return () => { live = false }
  }, [refObj.runId, selectedNodeId])

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
  const dag = vm ? layoutRunDag(vm.nodes) : null

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

      {vm && vm.totalCount > 0 && (
        <div>
          <button type="button" aria-expanded={graphOpen} onClick={() => setGraphOpen((open) => !open)}
            className="inline-flex items-center gap-xs text-xs text-primary hover:underline">
            <ChevronDown size={13} className={graphOpen ? '' : '-rotate-90'} />
            {graphOpen ? 'Hide run graph' : 'Inspect run graph'}
          </button>
          {graphOpen && dag && <div role="region" aria-label="Workflow nodes" className="mt-s max-h-72 overflow-auto rounded-lg border border-outline-variant/50 bg-surface-low p-s">
            <div style={{ width: dag.width, minWidth: '100%' }}><DagView nodes={dag.nodes} edges={dag.edges} width={dag.width} height={dag.height} onNodeClick={setSelectedNode} /></div>
            {selectedNode && <div className="mt-s border-t border-outline-variant/50 pt-s text-xs text-on-surface-var">
              <p className="font-medium text-on-surface">{selectedNode}</p>
              {nodeError ? <p role="alert" className="mt-xs text-warning">{nodeError}</p>
                : nodeDetail ? <pre className="mt-xs max-h-48 overflow-auto whitespace-pre-wrap break-words font-mono">{JSON.stringify(nodeDetail, null, 2)}</pre>
                  : <p className="mt-xs">Loading node detail…</p>}
              <TextLink href={`#/workflows/runs/${encodeURIComponent(refObj.runId)}?node=${encodeURIComponent(selectedNodeId)}`} size="xs">Open full inspector</TextLink>
            </div>}
          </div>}
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
      {vm?.nodes.filter((node) => node.state === 'waiting' || node.state === 'failed').map((node) => (
        <TextLink key={node.instance_path} size="xs" href={`#/workflows/runs/${encodeURIComponent(refObj.runId)}?node=${encodeURIComponent(node.node_id || node.instance_path)}`}>
          {node.node_id || node.instance_path}: {node.state === 'waiting' ? 'Waiting for an answer' : 'Failed'}
        </TextLink>
      ))}

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
