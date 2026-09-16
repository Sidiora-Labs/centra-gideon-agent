import { useEffect, useState } from 'react'
import { ScanSearch, Link2, Package } from 'lucide-react'
import { SidePanel } from '../../shared/ui/SidePanel'
import { Skeleton, LoadingStatus } from '../../shared/ui/ListScaffold'
import { InlineError } from '../../shared/ui/InlineError'
import { api, ApiError, type NodeInspect } from '../../shared/data/api'
import { accentChip } from '../../shared/theme/accent'
import { ledgerRowDetail, ledgerRowKey } from './ledgerRowDetail'

export function NodeInspectorDrawer({ runId, nodeId, onClose }: {
  runId: string
  nodeId: string
  onClose: () => void
}) {
  const [data, setData] = useState<NodeInspect | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    setData(null)
    api.workflowRunNodeInspect(runId, nodeId)
      .then((d) => { if (live) setData(d) })
      .catch((e) => {
        if (!live) return
        if (e instanceof ApiError && e.status === 409) {
          setError('This node has not finished yet — there is nothing to reconstruct until it reaches a terminal state.')
        } else if (e instanceof ApiError && e.status === 404) {
          setError('This node could not be found. The run may have been deleted.')
        } else {
          setError(e instanceof Error ? e.message : 'Could not load this node.')
        }
      })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId, nodeId])

  return (
    <SidePanel
      fillHeight
      storeKey="wf-node-inspect-w"
      icon={<ScanSearch size={18} className="text-primary" />}
      title={<span className="font-mono text-[1.0625rem]">{nodeId}</span>}
      onClose={onClose}
    >
      <div data-testid="node-inspector-body" className="flex flex-col gap-l">
        {loading ? (
          <div role="status" aria-busy="true"  className="flex flex-col gap-l">
        <LoadingStatus what="node detail" />
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : error ? (
          <InlineError icon multiline>{error}</InlineError>
        ) : data ? (
          <NodeInspectBody data={data} />
        ) : null}
      </div>
    </SidePanel>
  )
}

function refOf(v: unknown): string | null {
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>
    if (typeof o.ref === 'string') return o.ref
    if (typeof o.artifact_ref === 'string') return o.artifact_ref
  }
  return null
}

function RefChip({ icon: Icon, label, value }: { icon: typeof Link2; label: string; value: string }) {
  return (
    <span
      data-testid="ref-chip"
      data-type="caption" className="inline-flex items-center gap-1.5 rounded-pill bg-surface-high px-2.5 py-1 font-mono text-on-surface-var"
      title={`${label} — stored out-of-line; not fetched`}
    >
      <Icon size={13} className="shrink-0 text-on-surface-low" />
      {value}
    </span>
  )
}

function FieldBlock({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-xs">
      <h3 data-type="label-m" className="text-on-surface-low">{label}</h3>
      {children}
    </section>
  )
}

function CodeBlock({ text, testid, maxH = 'max-h-72' }: { text: string; testid?: string; maxH?: string }) {
  return (
    <pre
      data-testid={testid}
      data-type="caption" className={`${maxH} overflow-auto rounded-md bg-surface px-3 py-2 text-on-surface whitespace-pre-wrap break-words`}
    >
      {text}
    </pre>
  )
}

function toJson(v: unknown): string {
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

function NodeInspectBody({ data }: { data: NodeInspect }) {
  const promptRef = refOf(data.resolved_prompt)
  const outputRef = refOf(data.output)
  const inputKeys = Object.keys(data.resolved_inputs ?? {})

  return (
    <>
      { }
      <div data-type="caption" className="flex items-center gap-m">
        <span data-type="label-m" className="text-on-surface-low">state</span>
        <span className="text-on-surface">{data.state}</span>
        <span
          data-testid="cached-badge"
          data-type="caption" className="inline-flex items-center rounded-pill px-2 py-0.5"
          style={data.cached
            ? accentChip
            : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-low)' }}
          title={data.cached ? 'Output served from the resume cache' : 'Freshly produced this run'}
        >
          {data.cached ? 'cached' : 'fresh'}
        </span>
      </div>

      <FieldBlock label="Resolved prompt">
        {promptRef !== null ? (
          <RefChip icon={Link2} label="prompt ref" value={promptRef} />
        ) : (
          <CodeBlock testid="resolved-prompt" text={String(data.resolved_prompt ?? '')} />
        )}
      </FieldBlock>

      <FieldBlock label="Resolved inputs">
        {inputKeys.length === 0 ? (
          <p data-type="caption" className="text-on-surface-low">No inputs bound.</p>
        ) : (
          <CodeBlock testid="resolved-inputs" text={toJson(data.resolved_inputs)} />
        )}
      </FieldBlock>

      <FieldBlock label="Output">
        {outputRef !== null ? (
          <RefChip icon={Package} label="artifact ref" value={outputRef} />
        ) : (
          <CodeBlock testid="output" text={typeof data.output === 'string' ? data.output : toJson(data.output)} />
        )}
      </FieldBlock>

      <FieldBlock label={`Attempts${data.attempts.length ? ` (${data.attempts.length})` : ''}`}>
        {data.attempts.length === 0 ? (
          <p data-type="caption" className="text-on-surface-low">No attempt records.</p>
        ) : (
          <ol data-testid="attempts" className="flex flex-col gap-xs">
            {data.attempts.map((a, i) => {
              const status = typeof a.status === 'string' ? a.status
                : typeof a.state === 'string' ? a.state : 'attempt'
              return (
                <li key={i} data-type="caption" className="flex items-center gap-s rounded-md bg-surface px-2.5 py-1.5">
                  <span className="tabular-nums text-on-surface-low">#{i + 1}</span>
                  <span className="text-on-surface">{status}</span>
                </li>
              )
            })}
          </ol>
        )}
      </FieldBlock>

      {
}
      <FieldBlock label={`Ledger events${data.ledger_events.length ? ` (${data.ledger_events.length})` : ''}`}>
        {data.ledger_events.length === 0 ? (
          <p data-type="caption" className="text-on-surface-low">No ledger events for this node.</p>
        ) : (
          <ul data-testid="ledger-events" className="max-h-72 overflow-auto flex flex-col gap-xs">
            {data.ledger_events.map((e, i) => {
              const row = ledgerRowDetail(e)
              return (
                <li
                  key={ledgerRowKey(e, i)}
                  data-testid="ledger-event"
                  data-type="caption" className="flex flex-col gap-xs rounded-md bg-surface px-2.5 py-1.5"
                >
                  <div className="flex flex-wrap items-center gap-s">
                    <span data-testid="ledger-kind" className="font-mono text-on-surface-var">{row.kind}</span>
                    {row.sha ? (
                      <span data-testid="ledger-sha" className="font-mono text-on-surface break-all">{row.sha}</span>
                    ) : null}
                    {row.impact ? (
                      <span
                        data-testid="ledger-impact"
                        className="inline-flex items-center rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low"
                      >
                        {row.impact}
                      </span>
                    ) : null}
                  </div>
                  {
}
                  {row.rationale ? (
                    <p data-testid="ledger-rationale" className="text-on-surface break-words">{row.rationale}</p>
                  ) : null}
                </li>
              )
            })}
          </ul>
        )}
      </FieldBlock>
    </>
  )
}
