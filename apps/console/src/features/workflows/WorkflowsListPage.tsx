import { useCallback, useEffect, useMemo, useState } from 'react'
import { Play, Search, Sparkles, Trash2, Workflow } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { EmptyState, ListRow, Loading, LoadError } from '../../shared/ui/ListScaffold'
import { WindowedList } from '../../shared/ui/WindowedList'
import { ListControls } from '../../shared/ui/ListControls'
import { HeaderActions, HeaderControl, HeaderSegmented } from '../../shared/ui/HeaderActions'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Button } from '../../shared/ui/Button'
import { PresetEmptyState } from '../../shared/ui/PresetEmptyState'
import { api, ApiError, type WorkflowDef, type WorkflowDefSummary, type WorkflowSurfacingFinding, type WorkflowSurfacingRow } from '../../shared/data/api'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { confirm, confirmDelete, promptForm, promptInput } from '../../shared/ui/dialog'
import { notify } from '../../app/shell/appSdk'
import { fmtElapsed, isTerminal, runLook } from './workflowMeta'
import { coerceInputs, inputFields, startsWithoutInput } from './templateStart'
import { preflightRemediations } from './preflightRemediation'
import { rankWorkflowDefinitions } from './templateSuggest'
import { roundPlan, roundPlanText, roundPlanWithInputs } from './roundPlan'
import { workflowPresets } from './workflowPresets'
import { cadenceLabel, findingsByDef, freshnessLook, modeLook, needsAttention, packChips } from './surfacingMeta'
import { PageTitle } from '../../shared/ui/PageTitle'

const TABS = [
  { key: 'runs', label: 'Runs' },
  { key: 'defs', label: 'Definitions' },
]

export function WorkflowsListPage({ navigate, query: routeQuery, setQuery }: RouteProps) {
  const [tab, setTab] = useQueryParam(routeQuery, setQuery, 'tab', 'runs', { replace: true })
  const [q, setQ] = useQueryParam(routeQuery, setQuery, 'q', '', { replace: true })
  const [discovery, setDiscovery] = useState<{ intent: string; choices: WorkflowDefSummary[]; details: Record<string, WorkflowDef> } | null>(null)

  const { data: defsData, error: defsErr, loading: defsLoading, stale: defsStale } =
    useQuery('workflows:defs', () => api.workflowDefs().then((d) => d.defs))
  const { data: runsData, error: runsErr, loading: runsLoading, stale: runsStale } =
    useQuery('workflows:runs', () => api.workflowRuns({ limit: 100 }).then((r) => r.runs))
  const surfacingQ = useQuery('workflows:surfacing', () => api.workflowSurfacing()
    .catch(() => ({ defs: [] as WorkflowSurfacingRow[], total: 0, findings: [] as WorkflowSurfacingFinding[] })))

  const defs = defsData ?? []
  const runs = runsData ?? []
  const findings = surfacingQ.data?.findings ?? []
  const surfacing = useMemo(
    () => Object.fromEntries((surfacingQ.data?.defs ?? []).map((row) => [row.name, row])),
    [surfacingQ.data],
  ) as Record<string, WorkflowSurfacingRow>
  const loading = tab === 'defs' ? defsLoading : runsLoading
  const stale = tab === 'defs' ? defsStale : runsStale

  const load = useCallback(() => {
    invalidateKeys('workflows:', true)
  }, [])

  const filteredRuns = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const matched = needle
      ? runs.filter((r) => `${r.workflow_name}\n${r.id}\n${r.status}`.toLowerCase().includes(needle))
      : runs
    const rank = (s: string) => (s === 'needs_input' ? 0 : s === 'running' || s === 'paused' ? 1 : 2)
    return [...matched].sort((a, b) => rank(a.status) - rank(b.status))
  }, [runs, q])

  const byDef = useMemo(() => findingsByDef(findings), [findings])

  const presets = useMemo(() => workflowPresets(defs), [defs])

  const filteredDefs = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const matched = needle
      ? defs.filter((d) => `${d.name}\n${d.description}\n${d.tags.join(' ')}`.toLowerCase().includes(needle))
      : defs
    const rank = (name: string) =>
      needsAttention(surfacing[name] ?? { overdue: false }, (byDef[name] ?? []).length) ? 0 : 1
    return [...matched].sort((a, b) => rank(a.name) - rank(b.name))
  }, [defs, q, surfacing, byDef])

  const start = useCallback(async (name: string) => {
    let def: WorkflowDef | null = null
    try {
      def = (await api.workflowDef(name)).definition
    } catch {
    }

    const plan = def ? roundPlan(def) : null
    let inputs: Record<string, unknown> | undefined
    if (def && !startsWithoutInput(def.inputs)) {
      const fields = inputFields(def.inputs)
      const example = def.metadata?.steering_examples?.find((e) => e.event === 'kickoff')
      const answers = await promptForm({
        title: `Run ${name}`,
        body: [example?.description ? `For example: ${example.description}` : def.description,
          plan ? `Round plan to review:\n${roundPlanText(plan)}` : ''].filter(Boolean).join('\n\n'),
        fields,
        confirmLabel: 'Run',
      })
      if (answers === null) return
      inputs = coerceInputs(answers, def.inputs)
      if (plan && !(await confirm({ title: `Approve ${name} round plan?`, body: roundPlanText(roundPlanWithInputs(plan, inputs)), confirmLabel: 'Approve and run' }))) return
    } else {
      const approved = await confirm({
        title: `Run ${name}?`,
        body: [def?.description || 'Start this workflow with its saved definition and defaults.',
          plan ? `Round plan:\n${roundPlanText(plan)}` : ''].filter(Boolean).join('\n\n'),
        confirmLabel: 'Start run',
      })
      if (!approved) return
    }

    try {
      const res = await api.startWorkflowRun(inputs ? { name, inputs } : { name })
      navigate(`workflows/runs/${res.run_id}`)
    } catch (e) {
      const base = e instanceof Error ? e.message : 'Could not start the workflow'
      const fixes = preflightRemediations(e instanceof ApiError ? e.detail : undefined)
      notify(fixes.length ? `${base} — ${fixes.join('; ')}` : base, 'error')
    }
  }, [navigate])

  const startFromTemplate = useCallback(async () => {
    const intent = await promptInput({
      title: 'Start from template',
      label: 'What do you want to do?',
      placeholder: 'e.g. fix the login bug, or research vector databases',
      required: true,
      confirmLabel: 'Continue',
    })
    if (!intent) return
    const choices = rankWorkflowDefinitions(intent, defs, surfacing).slice(0, 4).map((match) => match.definition)
    const details = Object.fromEntries((await Promise.all(choices.map(async (choice) => {
      try { return [choice.name, (await api.workflowDef(choice.name)).definition] as const }
      catch { return null }
    }))).filter((entry): entry is readonly [string, WorkflowDef] => entry !== null))
    setDiscovery({ intent, choices, details })
    setTab('defs')
    setQ('')
  }, [defs, surfacing, setTab, setQ])

  const remove = useCallback(async (name: string) => {
    const ok = await confirmDelete('workflow definition', name, {
      body: 'Existing runs keep their own copy of the spec and are unaffected.',
    })
    if (!ok) return
    try {
      await api.deleteWorkflowDef(name)
      load()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not delete the definition', 'error')
    }
  }, [load])

  const [armed, setArmed] = useState<string | null>(null)
  useEffect(() => {
    if (!armed) return
    const t = window.setTimeout(() => setArmed(null), 4000)
    return () => window.clearTimeout(t)
  }, [armed])

  const removeRun = useCallback(async (id: string) => {
    try {
      await api.deleteWorkflowRun(id)
      setArmed(null)
      load()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not delete the run', 'error')
    }
  }, [load])

  const needingInput = runs.filter((r) => r.status === 'needs_input').length

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={<div className="flex min-w-0 items-center gap-m">
          {
}
          <PageTitle>Workflows</PageTitle>
          {needingInput > 0 && (
            <span data-type="caption" className="shrink-0 text-warning">{needingInput} waiting on you</span>
          )}
        </div>}
        right={<HeaderActions>
          <HeaderSegmented options={TABS} value={tab} onChange={setTab} ariaLabel="Workflows view" />
          <HeaderControl icon={Sparkles} label="Start from template" variant="primary" priority="primary"
            onClick={startFromTemplate} hint="Describe what you want to do; we'll pick the template" />
        </HeaderActions>}
      />
      <ListControls search={{ value: q, onChange: setQ, placeholder: 'Search runs and definitions', label: 'Search workflows' }}
        results={{ count: tab === 'defs' ? filteredDefs.length : filteredRuns.length, noun: tab === 'defs' ? 'definitions' : 'runs', active: !!q.trim() }}
        stale={stale} />
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {tab === 'defs' && discovery && (
          <section aria-label="Workflow matches" className="mb-l rounded-lg border border-outline-variant/50 bg-surface-container p-m">
            <div className="flex items-start justify-between gap-m">
              <div>
                <div data-type="body-m" className="text-on-surface">Matches for “{discovery.intent}”</div>
                <div data-type="caption" className="text-on-surface-low">Choose a saved workflow to review its inputs and start it.</div>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setDiscovery(null)}>Clear</Button>
            </div>
            {discovery.choices.length === 0 ? (
              <p data-type="body-s" className="mt-m text-on-surface-low">No matching workflow found. Browse the definitions below or try a different description.</p>
            ) : (
              <div className="mt-m flex flex-col gap-s">
                {discovery.choices.map((choice) => {
                  const detail = discovery.details[choice.name]
                  const required = Object.entries(detail?.inputs ?? {}).filter(([, param]) => param.required).map(([name]) => name)
                  return <div key={choice.name} className="flex flex-wrap items-center gap-m rounded-md bg-surface px-m py-s">
                    <div className="min-w-0 flex-1">
                      <div data-type="body-s" className="text-on-surface">{choice.name} · v{choice.version}</div>
                      <div data-type="caption" className="text-on-surface-low">{detail?.description || choice.description || 'Saved workflow'}</div>
                      <div data-type="caption" className="text-on-surface-low">{required.length ? `Needs: ${required.join(', ')}` : 'No required inputs'}</div>
                    </div>
                    <Button variant="secondary" size="sm" onClick={() => { void start(choice.name) }}>Review and start</Button>
                  </div>
                })}
              </div>
            )}
          </section>
        )}
        {loading ? <Loading what="workflows" /> : tab === 'defs' ? (
          defsErr ? (
            <LoadError what="workflow definitions" error={defsErr} onRetry={load} />
          ) : filteredDefs.length === 0 ? (
            q ? (
              <EmptyState icon={Search} title="No matching definitions" hint="Try a different search." />
            ) : (
              <EmptyState
                icon={Workflow}
                title="No workflow definitions yet"
                hint="A workflow is a repeatable plan an agent runs step by step. Start from a template and describe what you want, or ask in chat to author one."
                action={{ label: 'Start from template', onClick: startFromTemplate, icon: Sparkles }}
              />
            )
          ) : (
            <div className="flex flex-col gap-xs">
              {
}
              {filteredDefs.map((d, i) => (
                <ListRow key={`${d.source}:${d.name}`} index={i} onClick={() => navigate(`workflows/defs/${d.name}`)} label={d.name}>
                  <div className="flex min-w-0 flex-1 items-center gap-m">
                    <Workflow size={15} className="shrink-0 text-on-surface-low" />
                    <div className="min-w-0 flex-1">
                      <div data-type="body-m" className="truncate text-on-surface">{d.name}</div>
                      {(() => {
                        const row = surfacing[d.name]
                        const defFindings = byDef[d.name] ?? []
                        if (defFindings.length > 0) {
                          return (
                            <div data-type="caption" className="truncate text-warning" title={defFindings.map((f) => f.detail).join(' · ')}>
                              {defFindings[0].detail}
                            </div>
                          )
                        }
                        const cadence = row ? cadenceLabel(row) : ''
                        const subtitle = [d.description, cadence].filter(Boolean).join(' · ')
                        return subtitle ? <div data-type="caption" className="truncate text-on-surface-low">{subtitle}</div> : null
                      })()}
                    </div>
                    {(() => {
                      const row = surfacing[d.name]
                      if (!row) return null
                      const nodes = []
                      if (row.cadence_days > 0) {
                        const look = freshnessLook(row.freshness)
                        const Icon = look.icon
                        nodes.push(
                          <span key="fresh" data-type="caption" className={`flex shrink-0 items-center gap-xs ${look.tone}`} title={look.hint}>
                            <Icon size={12} /> {look.label}
                          </span>,
                        )
                      }
                      const mode = modeLook(row.surface_mode)
                      const ModeIcon = mode.icon
                      nodes.push(
                        <span key="mode" data-type="caption" className={`flex shrink-0 items-center gap-xs ${mode.tone}`} title={mode.hint}>
                          <ModeIcon size={12} /> {mode.label}
                        </span>,
                      )
                      for (const pack of packChips(row)) {
                        nodes.push(
                          <span key={`pack-${pack}`} data-type="caption" className="shrink-0 text-on-surface-low" title={`Pack: ${pack}`}>
                            {pack}
                          </span>,
                        )
                      }
                      return nodes
                    })()}
                    <span data-type="caption" className="shrink-0 text-on-surface-low">v{d.version}</span>
                    {d.source === 'bundled' && <span data-type="caption" className="shrink-0 text-on-surface-low">bundled</span>}
                    <QuietButton onClick={(e) => { e.stopPropagation(); start(d.name) }} title={`Run ${d.name}`}>
                      <Play size={13} /> Run
                    </QuietButton>
                    {d.source !== 'bundled' && (
                      <QuietButton onClick={(e) => { e.stopPropagation(); remove(d.name) }} title={`Delete ${d.name}`}>
                        <Trash2 size={13} />
                      </QuietButton>
                    )}
                  </div>
                </ListRow>
              ))}
            </div>
          )
        ) : runsErr ? (
          <LoadError what="workflow runs" error={runsErr} onRetry={load} />
        ) : filteredRuns.length === 0 ? (
          q ? (
            <EmptyState icon={Search} title="No matching runs" hint="Try a different search." />
          ) : presets.length > 0 ? (
            <PresetEmptyState
              title="No workflow runs yet"
              hint="A run is one execution of a workflow — every step, its output, and where it stopped. Pick a starting point and the next screen asks for what it needs."
              presets={presets}
              onPick={(template) => { void start(template) }}
              footer={
                <Button variant="ghost" size="sm" onClick={() => setTab('defs')}>
                  <Workflow size={15} /> Browse all definitions
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={Workflow}
              title="No workflow runs yet"
              hint="A run is one execution of a workflow — every step, its output, and where it stopped. Pick a definition to start your first."
              action={{ label: 'Browse definitions', onClick: () => setTab('defs'), icon: Workflow }}
            />
          )
        ) : (
          <WindowedList
            items={filteredRuns}
            rowKey={(r) => r.id}
            rowHeights="variable"
            estimateRowHeight={64}
            gap={4}
            noun="runs"
            findHint="use the Search runs and definitions field above."
            className="flex flex-col gap-xs"
          >
            {(r, i, listCtx) => {
              const look = runLook(r.status)
              const Icon = look.icon
              const elapsed = fmtElapsed(r.elapsed_seconds)
              return (
                <ListRow key={r.id} index={listCtx.windowed ? 0 : i} onClick={() => navigate(`workflows/runs/${r.id}`)} label={`${r.workflow_name} — run ${r.id}`}>
                  <div className="flex min-w-0 flex-1 items-center gap-m">
                    <Icon size={15} className={`shrink-0 ${look.tone}${look.spin ? ' animate-spin' : ''}`} />
                    <div className="min-w-0 flex-1">
                      <div data-type="body-m" className="truncate text-on-surface">{r.workflow_name}</div>
                      <div data-type="caption" className="truncate text-on-surface-low">
                        {look.label}{r.error_message ? ` · ${r.error_message}` : ''}
                      </div>
                    </div>
                    {elapsed && <span data-type="caption" className="shrink-0 text-on-surface-low tabular-nums">{elapsed}</span>}
                    <span data-type="caption" className="shrink-0 font-mono text-on-surface-low">{r.id}</span>
                    {
}
                    {isTerminal(r.status) && (
                      armed === r.id ? (
                        <QuietButton
                          onClick={(e) => { e.stopPropagation(); removeRun(r.id) }}
                          title="Click again to delete this run and its artifacts"
                        >
                          <Trash2 size={13} className="text-danger" /> Delete?
                        </QuietButton>
                      ) : (
                        <QuietButton
                          onClick={(e) => { e.stopPropagation(); setArmed(r.id) }}
                          title={`Delete run ${r.id}`}
                        >
                          <Trash2 size={13} />
                        </QuietButton>
                      )
                    )}
                  </div>
                </ListRow>
              )
            }}
          </WindowedList>
        )}
      </div>
    </div>
  )
}
