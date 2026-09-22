import { useCallback, useEffect, useMemo, useState } from 'react'
import { ArrowLeft, Play, Sparkles, RotateCcw } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { Loading } from '../../shared/ui/ListScaffold'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Button } from '../../shared/ui/Button'
import { HeaderActions } from '../../shared/ui/HeaderActions'
import { Segmented } from '../../shared/ui/Segmented'
import { Field, TextInput } from '../../shared/ui/forms'
import { Toggle } from '../../shared/ui/Toggle'
import { PageTitle } from '../../shared/ui/PageTitle'
import { SchemaFieldDisclosure } from '../tools/schema'
import {
  api,
  type WorkflowDef,
  type WorkflowNode,
  type WorkflowVersionRow,
  type WorkflowVersionOp,
  type WorkflowMaturity,
  type WorkflowLedgerRow,
} from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'

interface FlatNode { depth: number; kind: string; id: string; label: string; summary: string }

function flatten(node: WorkflowNode, depth = 0, label = ''): FlatNode[] {
  const cfg = node.config ?? {}
  const summary =
    typeof cfg.prompt === 'string' ? cfg.prompt
      : typeof cfg.expr === 'string' ? cfg.expr
      : typeof cfg.provider === 'string' ? `provider: ${cfg.provider}`
      : typeof cfg.kind === 'string' ? `${cfg.kind} gate`
      : ''
  const out: FlatNode[] = [{
    depth,
    kind: node.kind,
    id: node.id ?? '',
    label,
    summary: typeof summary === 'string' ? summary.replace(/\s+/g, ' ').slice(0, 140) : '',
  }]
  for (const child of node.children ?? []) out.push(...flatten(child, depth + 1))
  if (node.body) out.push(...flatten(node.body, depth + 1, 'body'))
  for (const [caseLabel, caseNode] of Object.entries(node.cases ?? {})) {
    out.push(...flatten(caseNode, depth + 1, `case ${caseLabel}`))
  }
  if (node.default) out.push(...flatten(node.default, depth + 1, 'default'))
  return out
}

function MaturityBadge({ maturity }: { maturity: WorkflowMaturity }) {
  const strong = maturity.level >= 2
  const tone = strong ? 'var(--color-primary)' : 'var(--color-on-surface-low)'
  return (
    <span
      data-type="caption" className="inline-flex shrink-0 items-center rounded-pill px-2 py-0.5"
      style={{ background: `color-mix(in srgb, ${tone} 14%, transparent)`, color: tone }}
      title={`Maturity L${maturity.level}: ${maturity.clean_runs} clean run${maturity.clean_runs === 1 ? '' : 's'}`
        + (maturity.evaluator_rejected ? ', gate has rejected a bad run' : ', gate not yet proven')}
    >
      {maturity.label} · L{maturity.level}
    </span>
  )
}

export function WorkflowDefDetail({ name, onBack, onStarted }: {
  name: string
  onBack: () => void
  onStarted: (runId: string) => void
}) {
  const [def, setDef] = useState<WorkflowDef | null>(null)
  const [loading, setLoading] = useState(true)
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [starting, setStarting] = useState(false)
  const [tab, setTab] = useState<'steps' | 'versions' | 'ledger'>('steps')
  const [versions, setVersions] = useState<WorkflowVersionRow[]>([])
  const [pinned, setPinned] = useState<number | null>(null)
  const [maturity, setMaturity] = useState<WorkflowMaturity | null>(null)
  const [diffOps, setDiffOps] = useState<WorkflowVersionOp[] | null>(null)
  const [ledger, setLedger] = useState<WorkflowLedgerRow[] | null>(null)
  const [refining, setRefining] = useState(false)
  const [publishSaving, setPublishSaving] = useState(false)

  const loadVersions = useCallback(() => {
    api.workflowVersions(name)
      .then((v) => {
        setVersions(v.versions)
        setPinned(v.pinned)
        setMaturity(v.maturity)
        if (v.versions.length >= 2) {
          const [a, b] = [v.versions[v.versions.length - 2].version, v.versions[v.versions.length - 1].version]
          api.workflowVersionDiff(name, a, b).then((d) => setDiffOps(d.ops)).catch(() => setDiffOps(null))
        } else {
          setDiffOps(null)
        }
      })
      .catch(() => { setVersions([]); setPinned(null); setMaturity(null) })
  }, [name])

  useEffect(() => {
    let alive = true
    setLoading(true)
    api.workflowDef(name)
      .then((d) => { if (alive) setDef(d.definition) })
      .catch(() => { if (alive) setDef(null) })
      .finally(() => { if (alive) setLoading(false) })
    loadVersions()
    return () => { alive = false }
  }, [name, loadVersions])

  useEffect(() => {
    if (tab === 'ledger' && ledger === null) {
      api.workflowLedger(name).then((l) => setLedger(l.runs)).catch(() => setLedger([]))
    }
  }, [tab, ledger, name])

  const rows = useMemo(() => (def ? flatten(def.root) : []), [def])
  const declared = useMemo(() => Object.entries(def?.inputs ?? {}), [def])
  const inputFields = useMemo(() => declared.map(([key, input]) => [key, {
    type: input.type,
    default: input.default,
    'x-meta': { help: input.help, tags: (input as { tags?: string[] }).tags },
  }] as [string, { type?: string; default?: unknown; 'x-meta': { help?: string; tags?: string[] } }]), [declared])
  const requiredInputs = useMemo(() => declared.filter(([, input]) => input.required).map(([key]) => key), [declared])
  const published = def?.metadata?.a2a_published === true
  const handoffs = useMemo(
    () => (def?.metadata?.hands_off_to ?? []).filter((h) => (h?.target_def ?? '').trim()),
    [def],
  )

  const start = useCallback(async () => {
    setStarting(true)
    try {
      const payload: Record<string, unknown> = {}
      for (const [key, value] of Object.entries(inputs)) if (value !== '') payload[key] = value
      const res = await api.startWorkflowRun({ name, inputs: payload })
      onStarted(res.run_id)
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not start the workflow', 'error')
    } finally {
      setStarting(false)
    }
  }, [inputs, name, onStarted])

  const refine = useCallback(async () => {
    if (refining) return
    setRefining(true)
    try {
      const res = await api.refineWorkflow(name)
      if (res.run_id) onStarted(res.run_id)
      else notify('Refiner started')
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not start the refiner', 'error')
    } finally {
      setRefining(false)
    }
  }, [name, onStarted, refining])

  const togglePublish = useCallback(async (next: boolean) => {
    const previous = def?.metadata?.a2a_published === true
    setPublishSaving(true)
    setDef((d) => (d ? { ...d, metadata: { ...(d.metadata ?? {}), a2a_published: next } } : d))
    try {
      const res = await api.publishWorkflowToA2A(name, next)
      setDef((d) => (d ? { ...d, metadata: { ...(d.metadata ?? {}), a2a_published: res.a2a_published } } : d))
    } catch (e) {
      setDef((d) => (d ? { ...d, metadata: { ...(d.metadata ?? {}), a2a_published: previous } } : d))
      notify(e instanceof Error ? e.message : 'Could not change A2A publication', 'error')
    } finally {
      setPublishSaving(false)
    }
  }, [def, name])

  const rollback = useCallback(async (version: number) => {
    try {
      await api.repinWorkflowVersion(name, version)
      loadVersions()
    } catch (e) {
      notify(e instanceof Error ? e.message : 'Could not roll back', 'error')
    }
  }, [name, loadVersions])

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<div className="flex min-w-0 items-center gap-m">
          <QuietButton onClick={onBack} title="Back to workflows"><ArrowLeft size={13} /> Workflows</QuietButton>
          <PageTitle className="truncate">{name}</PageTitle>
          {def?.source === 'bundled' && <span data-type="caption" className="shrink-0 text-on-surface-low">bundled</span>}
          {maturity && <MaturityBadge maturity={maturity} />}
        </div>}
        right={def ? (
          <HeaderActions>
            {
}
            <QuietButton
              onClick={refine}
              title="Propose an improvement to this template from its run history"
              disabled={refining}
              disabledReason="A refinement is already in flight"
            >
              <Sparkles size={13} /> {refining ? 'Refining…' : 'Refine now'}
            </QuietButton>
            <Button onClick={start} loading={starting} disabled={starting}>
              <Play size={14} /> Run
            </Button>
          </HeaderActions>
        ) : undefined}
      />
      <div className="min-h-0 flex-1 overflow-y-auto p-l">
        {loading ? <Loading what="this workflow" /> : !def ? (
          <p data-type="body-s" className="text-on-surface-low">This definition could not be loaded.</p>
        ) : (
          <div className="mx-auto flex max-w-[var(--content-width)] flex-col gap-l">
            {def.description && <p data-type="body-s" className="text-on-surface">{def.description}</p>}

            <Segmented
              ariaLabel="Definition view"
              value={tab}
              onChange={(k) => setTab(k as 'steps' | 'versions' | 'ledger')}
              options={[
                { key: 'steps', label: 'Steps' },
                { key: 'versions', label: 'Versions' },
                { key: 'ledger', label: 'Run Ledger' },
              ]}
            />

            {tab === 'steps' && (
              <>
                {declared.length > 0 && (
                  <div className="flex flex-col gap-s">
                    <span data-type="title-m" className="text-on-surface">Inputs</span>
                    <SchemaFieldDisclosure fields={inputFields} required={requiredInputs} values={inputs}
                      renderField={([key]) => {
                        const meta = def.inputs?.[key]
                        return <Field
                          key={key}
                          label={`${key}${meta?.required ? ' *' : ''}`}
                          hint={meta?.help || (meta?.default !== undefined && meta.default !== null ? `Default: ${String(meta.default)}` : undefined)}
                        >
                          <TextInput
                            value={inputs[key] ?? ''}
                            onChange={(v) => setInputs((p) => ({ ...p, [key]: v }))}
                            placeholder={meta?.default !== undefined && meta.default !== null ? String(meta.default) : ''}
                            ariaLabel={key}
                          />
                        </Field>
                      }} />
                  </div>
                )}

                <div className="flex items-start justify-between gap-m">
                  <div className="min-w-0">
                    <span data-type="title-m" className="text-on-surface">Publish to A2A</span>
                    <p data-type="caption" className="text-on-surface-low">
                      {published
                        ? 'External agents holding an A2A token can see this template on the agent card and start it.'
                        : 'Off. This template is not on the A2A agent card and cannot be started by an external agent.'}
                    </p>
                  </div>
                  {
}
                  <Toggle
                    on={published}
                    onChange={togglePublish}
                    disabled={publishSaving}
                    label={`Publish ${name} to A2A`}
                  />
                </div>

                <div className="flex flex-col gap-xs">
                  <span data-type="title-m" className="text-on-surface">Steps</span>
                  {rows.map((r, i) => (
                    <div
                      key={`${r.depth}-${r.id || r.kind}-${i}`}
                      className="flex items-baseline gap-m py-2xs"
                      style={{ paddingLeft: `calc(${r.depth} * 1rem)` }}
                    >
                      <span data-type="caption" className="shrink-0 font-mono text-on-surface-low">{r.kind}</span>
                      <div className="min-w-0 flex-1">
                        {r.id && <span data-type="body-s" className="text-on-surface">{r.id}</span>}
                        {r.label && <span data-type="caption" className="ml-s text-on-surface-low">{r.label}</span>}
                        {r.summary && <div data-type="caption" className="truncate text-on-surface-low">{r.summary}</div>}
                      </div>
                    </div>
                  ))}
                </div>

                {handoffs.length > 0 && (
                  <div className="flex flex-col gap-xs">
                    <span data-type="title-m" className="text-on-surface">Hands off to</span>
                    {handoffs.map((h) => (
                      <div key={h.target_def} className="flex items-baseline gap-m py-2xs">
                        <span data-type="body-s" className="shrink-0 text-on-surface">{h.target_def}</span>
                        <div className="min-w-0 flex-1">
                          {h.condition && <span data-type="caption" className="text-on-surface-low">{h.condition}</span>}
                          {!!h.context_fields?.length && (
                            <div data-type="caption" className="text-on-surface-low">
                              carries {h.context_fields.join(', ')}
                            </div>
                          )}
                        </div>
                        {h.requires_user_request && (
                          <span data-type="caption" className="shrink-0 text-on-surface-low">only on request</span>
                        )}
                      </div>
                    ))}
                  </div>
                )}

                {def.metadata?.requirements && Object.keys(def.metadata.requirements).length > 0 && (
                  <div className="flex flex-col gap-xs">
                    <span data-type="title-m" className="text-on-surface">Requires</span>
                    {Object.entries(def.metadata.requirements).map(([group, items]) => (
                      <span key={group} data-type="caption" className="text-on-surface-low">
                        {group}: {items.join(', ')}
                      </span>
                    ))}
                  </div>
                )}
              </>
            )}

            {tab === 'versions' && (
              <div className="flex flex-col gap-l">
                <div className="flex flex-col gap-xs">
                  <span data-type="title-m" className="text-on-surface">Versions</span>
                  {versions.length === 0 ? (
                    <p data-type="caption" className="text-on-surface-low">No version history yet.</p>
                  ) : (
                    [...versions].reverse().map((v) => (
                      <div key={v.version} className="flex items-baseline gap-m py-2xs">
                        <span data-type="body-s" className="shrink-0 font-mono text-on-surface">v{v.version}</span>
                        <div className="min-w-0 flex-1">
                          <span data-type="caption" className="text-on-surface-low">{v.source}</span>
                          {v.created_at && <span data-type="caption" className="ml-s text-on-surface-low">{v.created_at}</span>}
                        </div>
                        {v.version === pinned ? (
                          <span data-type="caption" className="shrink-0 text-on-surface-low">pinned</span>
                        ) : (
                          <QuietButton onClick={() => rollback(v.version)} title={`Roll back to v${v.version}`}>
                            <RotateCcw size={12} /> Roll back
                          </QuietButton>
                        )}
                      </div>
                    ))
                  )}
                </div>

                {diffOps && diffOps.length > 0 && (
                  <div className="flex flex-col gap-xs">
                    <span data-type="title-m" className="text-on-surface">Latest change</span>
                    {diffOps.map((op, i) => (
                      <div key={`${op.op}-${op.node_id ?? ''}-${i}`} className="flex items-baseline gap-m py-2xs">
                        <span data-type="caption" className="shrink-0 font-mono text-on-surface-low">{op.op}</span>
                        <div data-type="body-s" className="min-w-0 flex-1 text-on-surface">
                          {op.node_id}
                          {op.fields?.length ? <span className="text-on-surface-low"> — {op.fields.join(', ')}</span> : null}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}

            {tab === 'ledger' && (
              <div className="flex flex-col gap-xs">
                <span data-type="title-m" className="text-on-surface">Run Ledger</span>
                {ledger === null ? (
                  <Loading what="the run ledger" />
                ) : ledger.length === 0 ? (
                  <p data-type="caption" className="text-on-surface-low">This template has no recorded runs yet.</p>
                ) : (
                  ledger.map((run) => (
                    <div key={run.run_id} className="flex items-baseline gap-m py-2xs">
                      <span data-type="caption" className="shrink-0 font-mono text-on-surface-low">{run.run_id}</span>
                      <div className="min-w-0 flex-1">
                        <span data-type="body-s" className="text-on-surface">{run.status}</span>
                        <span data-type="caption" className="ml-s text-on-surface-low">v{run.spec_version}</span>
                      </div>
                      <span data-type="caption" className="shrink-0 text-on-surface-low">
                        {run.totals?.steps_completed ?? 0} done
                        {run.totals?.steps_failed ? `, ${run.totals.steps_failed} failed` : ''}
                      </span>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
