import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronDown, ChevronRight, FolderGit2, GitBranch, MessageSquarePlus, MessageSquareCode, Package, Pause, Pencil, Play, RotateCcw, ScanSearch, Scale, SkipForward, X } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { Segmented } from '../../shared/ui/Segmented'
import { Loading } from '../../shared/ui/ListScaffold'
import { QuietButton } from '../../shared/ui/QuietButton'
import { SidePanel } from '../../shared/ui/SidePanel'
import { InlineError } from '../../shared/ui/InlineError'
import { api, partitionRunHistory, requireWriteAccepted, type WorkflowContinuation, type WorkflowNodeState, type WorkflowRunDetailData } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { confirm, promptForm } from '../../shared/ui/dialog'
import { PageTitle } from '../../shared/ui/PageTitle'
import { fmtElapsed, isNodeTerminal, isPrelaunch, isTerminal, itemProgress, nodeLabel, nodeLook, runLook } from './workflowMeta'
import { PolicyOverridesPanel } from './PolicyOverridesPanel'
import { byInstancePath } from './instancePathOrder'
import { buildTree, initialCollapsed, summarize, summaryLabel, visibleRows } from './nodeTree'
import { useWorkflowStream, type WorkflowLifecycleEvent } from './useWorkflowStream'
import { DagView } from '../tasks/DagView'
import { layoutRunDag } from './runDag'
import { tokenForNode } from './surfacingMeta'
import { revalidateNotice, revalidateSummary } from './revalidate'
import { WorkflowAsk } from './WorkflowAsk'
import { NodeInspectorDrawer } from './NodeInspectorDrawer'
import { SteeringPanel } from './SteeringPanel'
import { WorkspacePanel } from './WorkspacePanel'
import { OutboxPanel } from './OutboxPanel'
import { IntrospectPanel } from './IntrospectPanel'
import { LedgerRailsPanel } from './LedgerRailsPanel'
import { DeliverablePanel } from './DeliverablePanel'
import { ReviewTriagePanel } from './ReviewTriagePanel'
import { foldEvent, foldSnapshot } from './workflowFold'
import { EscalationPanel, isEscalationRecord } from './EscalationPanel'

function mergeCachedNodes(next: WorkflowRunDetailData, previous: WorkflowRunDetailData | null): WorkflowRunDetailData {
  if (!previous) return next
  const cached = new Map(previous.nodes
    .filter((node) => typeof node.cached === 'boolean')
    .map((node) => [node.instance_path, node.cached] as const))
  return {
    ...next,
    nodes: next.nodes.map((node) => node.cached === undefined && cached.has(node.instance_path)
      ? { ...node, cached: cached.get(node.instance_path) }
      : node),
  }
}

function foldRunEvent(run: WorkflowRunDetailData, event: WorkflowLifecycleEvent, data: unknown): WorkflowRunDetailData {
  const folded = foldEvent(foldSnapshot(run), event, data)
  return {
    ...run,
    status: folded.status as WorkflowRunDetailData['status'],
    spec_version: folded.specVersion,
    error: folded.error,
    attention: folded.attention,
    tokens: folded.tokens,
    elapsed_secs: folded.elapsedSecs,
    nodes: folded.nodes,
  }
}

function fanoutGraphPath(path: string): string {
  return path
    .replace(/#(\d+)/g, '.fanout[$1]')
    .replace(/@(\d+)/g, '.iteration[$1]')
}

function layoutWorkflowRunDag(nodes: WorkflowNodeState[], continuations: WorkflowContinuation[]) {
  const originalPath = new Map<string, string>()
  const graphNodes = nodes.map((node) => {
    const instance_path = fanoutGraphPath(node.instance_path)
    originalPath.set(instance_path, node.instance_path)
    return { ...node, instance_path }
  })
  const graphContinuations = continuations.map((continuation) => ({
    ...continuation,
    instance_path: fanoutGraphPath(continuation.instance_path),
  }))
  const dag = layoutRunDag(graphNodes, {
    continuations: graphContinuations,
    label: (n) => `${n.item_label ? `${n.node_id} · ${n.item_label}` : n.node_id}${n.cached ? ' · cached' : ''}`,
  })
  const restore = (path: string | undefined) => path ? (originalPath.get(path) ?? path) : undefined
  return {
    ...dag,
    nodes: dag.nodes.map((node) => ({ ...node, id: restore(node.id) as string })),
    edges: dag.edges.map((edge) => {
      const from = restore(edge.from)
      const to = restore(edge.to)
      return { ...edge, id: `${from ?? ''}->${to ?? ''}`, from, to }
    }),
  }
}

export function WorkflowRunDetail({ runId, onBack, initialInspectNodeId, onInspectorClose }: {
  runId: string
  onBack: () => void
  initialInspectNodeId?: string
  onInspectorClose?: () => void
}) {
  const [run, setRun] = useState<WorkflowRunDetailData | null>(null)
  const [conts, setConts] = useState<WorkflowContinuation[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [loadError, setLoadError] = useState('')
  const [inspectNodeId, setInspectNodeId] = useState<string | null>(initialInspectNodeId || null)
  const [steerOpen, setSteerOpen] = useState(false)
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [outboxOpen, setOutboxOpen] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [introspectOpen, setIntrospectOpen] = useState(false)
  const [railsOpen, setRailsOpen] = useState(false)
  const [showSuppressed, setShowSuppressed] = useState(false)
  const runHistoryId = useId()
  const pending = useRef<number | null>(null)

  const refetch = useCallback(async () => {
    const [status, continuations] = await Promise.allSettled([
      api.workflowRun(runId),
      api.workflowContinuations(runId),
    ])
    const failures: string[] = []
    if (status.status === 'fulfilled') setRun((previous) => mergeCachedNodes(status.value, previous))
    else failures.push(status.reason instanceof Error ? status.reason.message : 'Could not read this run.')
    if (continuations.status === 'fulfilled') setConts(continuations.value.continuations)
    else failures.push(continuations.reason instanceof Error ? continuations.reason.message : 'Could not read pending questions.')
    setLoadError(failures.join(' '))
    setLoading(false)
  }, [runId])

  useEffect(() => { refetch() }, [refetch])

  useEffect(() => { setInspectNodeId(initialInspectNodeId || null) }, [initialInspectNodeId])

  const scheduleRefetch = useCallback(() => {
    if (pending.current !== null) return
    pending.current = window.setTimeout(() => { pending.current = null; refetch() }, 250)
  }, [refetch])

  useEffect(() => () => { if (pending.current !== null) window.clearTimeout(pending.current) }, [])

  const live = !!run && !isTerminal(run.status)
  const { connected } = useWorkflowStream(runId, live, {
    onSnapshot: (snap) => { setRun((previous) => mergeCachedNodes(snap, previous)); setLoading(false) },
    onLifecycle: (event, data) => {
      setRun((current) => current ? foldRunEvent(current, event, data) : current)
      scheduleRefetch()
    },
  })

  const act = useCallback(async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true)
    setActionError('')
    try {
      requireWriteAccepted(await fn())
      await refetch()
      return true
    } catch (e) {
      const message = e instanceof Error ? e.message : `${label} failed`
      setActionError(message)
      notify(message, 'error')
      return false
    } finally {
      setBusy(false)
    }
  }, [refetch])

  const answer = useCallback(async (cont: WorkflowContinuation, value: unknown, alwaysAllow: boolean) => {
    if ((cont.ask.kind || 'approval') === 'approval') {
      await act(value ? 'Approve' : 'Deny', () => api.confirmWorkflowRun(runId, {
        verb: value ? 'approve' : 'reject', resume_token: cont.resume_token,
      }))
      return
    }
    await act('Answer', () => api.resumeWorkflowRun(runId, {
      answer: value, resume_token: cont.resume_token, always_allow: alwaysAllow,
    }))
  }, [act, runId])

  const rewind = useCallback(async (nodeId: string) => {
    const ok = await confirm({
      title: `Re-run "${nodeId}"?`,
      body: 'This node and everything that reads its output will run again. Previous outputs are archived, not lost.',
      confirmLabel: 'Re-run',
    })
    if (ok) await act('Rewind', () => api.rewindWorkflowRun(runId, { node_id: nodeId }))
  }, [act, runId])

  const runFrom = useCallback(async (nodeId: string) => {
    await act('Run from', () => api.workflowRunFrom(runId, { node_id: nodeId }))
  }, [act, runId])

  const editNodePrompt = useCallback(async (nodeId: string) => {
    const answers = await promptForm({
      title: `Edit "${nodeId}"`,
      body: revalidateNotice,
      fields: [{
        name: 'prompt',
        label: 'Instruction',
        type: 'textarea',
        placeholder: 'The new instruction for this stage.',
        required: true,
      }],
      confirmLabel: 'Apply edit',
    })
    if (answers === null) return
    await act('Edit', async () => {
      const res = await api.editWorkflowRun(runId, {
        ops: [{ kind: 'update_node', node_id: nodeId, fields: { prompt: answers.prompt } }],
      })
      if (res.ok === false || (res.issues?.length ?? 0) > 0) {
        throw new Error(res.issues?.[0]?.message ?? 'The edit was rejected.')
      }
      notify(revalidateSummary(res.preview))
      return res
    })
  }, [act, runId])

  const cancel = useCallback(async () => {
    const ok = await confirm({
      title: 'Cancel this run?',
      body: 'In-flight steps are stopped. Completed work is kept.',
      confirmLabel: 'Cancel run',
      danger: true,
    })
    if (ok) await act('Cancel', () => api.cancelWorkflowRun(runId))
  }, [act, runId])

  const fork = useCallback(async () => {
    await act('Fork', async () => {
      const res = await api.forkWorkflowRun(runId, { note: 'branched from the run view' })
      notify(`Forked to ${res.child_run_id}. Not isolated: ${res.shared_axes.length} shared axes.`)
      return res
    })
  }, [act, runId])

  const look = run ? runLook(run.status) : null
  const StatusIcon = look?.icon

  const nodes = useMemo(() => [...(run?.nodes ?? [])].sort(byInstancePath), [run])

  const rows = useMemo(() => buildTree(nodes), [nodes])

  const [view, setView] = useState<'list' | 'graph'>('list')

  const dag = useMemo(
    () => layoutWorkflowRunDag(nodes, conts),
    [nodes, conts],
  )

  const resolveGate = useCallback(
    async (instancePath: string, approved: boolean) => {
      const token = tokenForNode(
        conts.map((c) => ({ node_id: c.instance_path, resume_token: c.resume_token, expired: c.expired })),
        instancePath,
      )
      if (!token) { notify('That gate has no pending question.'); return }
      await act(approved ? 'Approve' : 'Deny', async () => {
        const res = await api.confirmWorkflowRun(runId, {
          verb: approved ? 'approve' : 'reject',
          resume_token: token,
        })
        requireWriteAccepted(res)
        notify(`Gate ${res.verb}d.`)
        return res
      })
    },
    [act, conts, runId],
  )
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const seeded = useRef<string>('')
  useEffect(() => {
    if (!run || seeded.current === run.run_id) return
    seeded.current = run.run_id
    setCollapsed(initialCollapsed(buildTree(run.nodes ?? []), run.nodes ?? []))
  }, [run])
  const toggle = useCallback((path: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])
  const partitionedRows = useMemo(
    () => partitionRunHistory(rows, (row) => row.node.state),
    [rows],
  )
  const visibleCollapsed = useMemo(() => {
    const visiblePaths = new Set(partitionedRows.visible.map((row) => row.node.instance_path))
    return new Set([...collapsed].filter((path) => visiblePaths.has(path)))
  }, [collapsed, partitionedRows.visible])
  const visibleRunRows = useMemo(
    () => visibleRows(partitionedRows.visible, visibleCollapsed),
    [partitionedRows.visible, visibleCollapsed],
  )
  const visibleSuppressedRows = useMemo(
    () => visibleRows(partitionedRows.suppressed, collapsed),
    [partitionedRows.suppressed, collapsed],
  )
  const listedRows = showSuppressed
    ? [...visibleRunRows, ...visibleSuppressedRows]
    : visibleRunRows

  useEffect(() => { setShowSuppressed(false) }, [runId])

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          <QuietButton onClick={onBack} title="Back to workflows"><ArrowLeft size={13} /> Workflows</QuietButton>
          {
}
          {run && <PageTitle className="truncate">{run.workflow}</PageTitle>}
          {
}
          {live && (
            <span data-type="caption" className="inline-flex shrink-0 items-center gap-1 text-on-surface-low">
              <span className="inline-block size-1.5 rounded-pill"
                style={{ background: connected ? 'var(--color-ok)' : 'var(--color-on-surface-low)' }} />
              {connected ? 'Streaming' : 'Connecting…'}
            </span>
          )}
          {look && StatusIcon && (
            <span data-type="caption" className={`inline-flex shrink-0 items-center gap-1 ${look.tone}`}>
              <StatusIcon size={13} className={look.spin ? 'animate-spin' : ''} /> {look.label}
            </span>
          )}
        </div>}
        right={run ? (
          <div className="flex items-center gap-xs">
            {
}
            <QuietButton onClick={() => setWorkspaceOpen((v) => !v)} ariaExpanded={workspaceOpen} title="Workspace — changed files and how to take this work">
              <FolderGit2 size={13} /> Workspace
            </QuietButton>
            {
}
            <QuietButton onClick={() => setOutboxOpen((v) => !v)} ariaExpanded={outboxOpen} title="Artifacts — what this run published, version diffs, and handing it files">
              <Package size={13} /> Artifacts
            </QuietButton>
            {
}
            <QuietButton onClick={() => setIntrospectOpen((v) => !v)} ariaExpanded={introspectOpen} title="Introspect — cost, latency, gates, timeline and proof">
              <ScanSearch size={13} /> Introspect
            </QuietButton>
            {
}
            <QuietButton onClick={() => setRailsOpen((v) => !v)} ariaExpanded={railsOpen} title="Rails — the per-step findings rail and the judge verdict/ROI rail from this run's ledger">
              <Scale size={13} /> Rails
            </QuietButton>
            {
}
            <QuietButton onClick={() => setReviewOpen((v) => !v)} ariaExpanded={reviewOpen} title="Review — accept or reject this run's line-anchored findings">
              <MessageSquareCode size={13} /> Review
            </QuietButton>
            {isPrelaunch(run.status) ? (
              <QuietButton onClick={() => act('Start', () => api.startWorkflowDraft(runId))} title="Start this draft workflow">
                <Play size={13} /> Start
              </QuietButton>
            ) : run.status === 'paused' ? (
              <>
                <QuietButton onClick={() => act('Resume', () => api.resumeWorkflowRun(runId, {}))} title="Resume this paused workflow">
                  <Play size={13} /> Resume
                </QuietButton>
                <QuietButton onClick={cancel} title="Cancel this run"><X size={13} /> Cancel</QuietButton>
              </>
            ) : !isTerminal(run.status) ? (
              <>
                <QuietButton onClick={() => setSteerOpen((v) => !v)} ariaExpanded={steerOpen} title="Steer this run — queue an instruction or accept a judge comment">
                  <MessageSquarePlus size={13} /> Steer
                </QuietButton>
                {run.status === 'running' && <QuietButton onClick={() => act('Pause', () => api.pauseWorkflowRun(runId))} title="Pause — in-flight steps finish">
                  <Pause size={13} /> Pause
                </QuietButton>}
                <QuietButton onClick={cancel} title="Cancel this run"><X size={13} /> Cancel</QuietButton>
              </>
            ) : (
              <QuietButton onClick={fork} title="Branch a new run from this one; the original is untouched">
                <GitBranch size={13} /> Fork
              </QuietButton>
            )}
          </div>
        ) : undefined}
      />

      <div className="flex min-h-0 flex-1">
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loading && !run ? <Loading what="this run" /> : !run ? (
          <InlineError icon multiline onRetry={refetch}>Couldn&rsquo;t load this run{loadError ? `: ${loadError}` : '.'}</InlineError>
        ) : (
          <div className="mx-auto flex max-w-[var(--content-width)] flex-col gap-l">
            { }
            {!isTerminal(run.status) && conts.map((c) => (
              <WorkflowAsk key={c.resume_token} continuation={c} runId={runId} busy={busy} onAnswer={answer} />
            ))}

            {actionError && <InlineError icon multiline onDismiss={() => setActionError('')}>{actionError}</InlineError>}

            {loadError && <InlineError icon multiline onRetry={refetch}>Couldn&rsquo;t refresh this run: {loadError}</InlineError>}

            {run.error && (
              <p data-type="body-s" className="text-danger">{run.error}</p>
            )}

            {isEscalationRecord(run.attention) && (
              <EscalationPanel escalation={run.attention} error={run.error} />
            )}

            <div data-type="caption" className="flex flex-wrap items-center gap-l text-on-surface-low">
              <span>run <span className="font-mono">{run.run_id}</span></span>
              <span>spec v{run.spec_version}</span>
              {run.tokens ? <span className="tabular-nums">{run.tokens.toLocaleString()} tokens</span> : null}
              {run.elapsed_secs ? <span className="tabular-nums">{fmtElapsed(run.elapsed_secs)} {isTerminal(run.status) ? 'duration' : 'elapsed'}</span> : null}
            </div>

            {
}
            {isPrelaunch(run.status) && (
              <PolicyOverridesPanel
                key={run.run_id}
                runId={runId}
                initial={run.policy_overrides ?? {}}
                onSaved={() => refetch()}
              />
            )}

            {
}
            {nodes.length > 0 && (
              <div className="flex items-center gap-xs">
                <Segmented
                  ariaLabel="Run view"
                  value={view}
                  onChange={(v) => setView(v as 'list' | 'graph')}
                  options={[
                    { key: 'list', label: 'List' },
                    { key: 'graph', label: 'Graph' },
                  ]}
                />
              </div>
            )}

            {view === 'graph' && dag.nodes.length > 0 ? (
              <div className="overflow-auto rounded-lg bg-surface-high p-s">
                <div style={{ width: dag.width, minWidth: '100%' }}>
                  <DagView
                    nodes={dag.nodes}
                    edges={dag.edges}
                    width={dag.width}
                    height={dag.height}
                    onNodeClick={(id) => toggle(id)}
                    onApprove={isTerminal(run.status) ? undefined : (id) => resolveGate(id, true)}
                    onDeny={isTerminal(run.status) ? undefined : (id) => resolveGate(id, false)}
                  />
                </div>
              </div>
            ) : null}

            <div className={`flex flex-col gap-xs${view === 'graph' ? ' hidden' : ''}`}>
              <div id={runHistoryId} className="flex flex-col gap-xs">
              {listedRows.map(({ node: n, depth, descendants, collapsible }) => {
                const nl = nodeLook(n.state)
                const NIcon = nl.icon
                const canReenter = !isTerminal(run.status) && !!n.node_id
                const isCollapsed = collapsed.has(n.instance_path)
                const summary = collapsible ? summarize(descendants, nodes) : null
                return (
                  <div
                    key={n.instance_path}
                    className="group flex items-center gap-m rounded-lg px-s py-xs hover:bg-surface-high"
                    style={{ paddingLeft: `calc(var(--space-s) + ${depth} * 1rem)` }}
                  >
                    {
}
                    {collapsible ? (
                      <button
                        type="button"
                        onClick={() => toggle(n.instance_path)}
                        className="shrink-0 text-on-surface-low transition-colors hover:text-on-surface"
                        title={isCollapsed ? `Show ${descendants.length} nested steps` : 'Collapse'}
                        aria-expanded={!isCollapsed}
                      >
                        {isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />}
                      </button>
                    ) : (
                      <span className="w-[14px] shrink-0" />
                    )}
                    <NIcon size={14} className={`shrink-0 ${nl.tone}${nl.spin ? ' animate-spin' : ''}`} />
                    <div className="min-w-0 flex-1">
                      <div className="flex min-w-0 items-center gap-s">
                        <span data-type="body-s" className="truncate text-on-surface">{nodeLabel(n)}</span>
                        {
}
                        {itemProgress(n) && (
                          <span data-type="caption" className="min-w-0 shrink truncate text-on-surface-low tabular-nums">
                            {itemProgress(n)}
                          </span>
                        )}
                        {
}
                        {isCollapsed && summary && (
                          <span data-type="caption" className="min-w-0 shrink truncate text-on-surface-low">
                            {summaryLabel(summary)}
                          </span>
                        )}
                      </div>
                      {(n.degraded_reason || n.failure?.cause_plain) && (
                        <div data-type="caption" className="truncate text-on-surface-low">
                          {n.degraded_reason || n.failure?.cause_plain}
                        </div>
                      )}
                      {
}
                      {n.failure?.remediation && (
                        <div data-type="caption" className="truncate text-on-surface-low">{n.failure.remediation}</div>
                      )}
                    </div>
                    <span data-type="caption" className={`shrink-0 ${nl.tone}`}>{nl.label}</span>
                    {(canReenter || (isNodeTerminal(n.state) && !!n.node_id)) && (
                      <span className="flex shrink-0 items-center gap-xs opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                        {
}
                        {isNodeTerminal(n.state) && !!n.node_id && (
                          <QuietButton onClick={() => setInspectNodeId(n.node_id)} title="Inspect this node — resolved prompt, inputs, output, attempts and ledger">
                            <ScanSearch size={12} />
                          </QuietButton>
                        )}
                        {canReenter && (
                          <>
                            {
}
                            <QuietButton onClick={() => editNodePrompt(n.node_id)} title="Edit this stage's instruction — re-validates the template's judge calibration">
                              <Pencil size={12} />
                            </QuietButton>
                            <QuietButton onClick={() => rewind(n.node_id)} title="Re-run this node and everything reading its output">
                              <RotateCcw size={12} />
                            </QuietButton>
                            <QuietButton onClick={() => runFrom(n.node_id)} title="Re-run only what comes after, keeping this output">
                              <SkipForward size={12} />
                            </QuietButton>
                          </>
                        )}
                      </span>
                    )}
                  </div>
                )
              })}
              </div>
              {partitionedRows.suppressed.length > 0 && (
                <button
                  type="button"
                  aria-controls={runHistoryId}
                  aria-expanded={showSuppressed}
                  onClick={() => setShowSuppressed((value) => !value)}
                  className="inline-flex h-7 self-start items-center gap-xs rounded-md px-s text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  data-type="caption"
                >
                  {showSuppressed ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  {showSuppressed
                    ? `Hide ${partitionedRows.suppressed.length} suppressed step${partitionedRows.suppressed.length === 1 ? '' : 's'}`
                    : `Show ${partitionedRows.suppressed.length} suppressed step${partitionedRows.suppressed.length === 1 ? '' : 's'}`}
                </button>
              )}
            </div>

            {
}
            <DeliverablePanel key={run.run_id} runId={runId} />
          </div>
        )}
      </div>

      {
}
      {inspectNodeId && (
        <NodeInspectorDrawer runId={runId} nodeId={inspectNodeId} onClose={() => {
          setInspectNodeId(null)
          onInspectorClose?.()
        }} />
      )}

      {
}
      {workspaceOpen && (
        <WorkspacePanel runId={runId} onClose={() => setWorkspaceOpen(false)} />
      )}

      {
}
      {outboxOpen && (
        <OutboxPanel runId={runId} onClose={() => setOutboxOpen(false)} />
      )}

      {
}
      {introspectOpen && (
        <IntrospectPanel runId={runId} onClose={() => setIntrospectOpen(false)} />
      )}

      {
}
      {railsOpen && (
        <SidePanel title="Ledger rails" icon={<Scale size={18} />} onClose={() => setRailsOpen(false)} fillHeight>
          <LedgerRailsPanel runId={runId} />
        </SidePanel>
      )}

      {
}
      {reviewOpen && (
        <SidePanel title="Review findings" icon={<MessageSquareCode size={18} />} onClose={() => setReviewOpen(false)} fillHeight>
          <ReviewTriagePanel runId={runId} onDispatched={refetch} />
        </SidePanel>
      )}

      {
}
      {run && steerOpen && !isTerminal(run.status) && (
        <SidePanel title="Steer run" icon={<MessageSquarePlus size={18} />} onClose={() => setSteerOpen(false)} fillHeight>
          <SteeringPanel runId={runId} projectId={run.project_id} nodes={run.nodes} onSteered={refetch} />
        </SidePanel>
      )}
      </div>
    </div>
  )
}
