import { useCallback, useEffect, useState } from 'react'
import { FileText, NotebookPen } from 'lucide-react'
import { Segmented } from '../../shared/ui/Segmented'
import { FormSkeleton } from '../../shared/ui/ListScaffold'
import { InlineError } from '../../shared/ui/InlineError'
import { Markdown } from '../../shared/ui/Markdown'
import { api, type WorkflowDeliverableAbsence, type WorkflowDeliverableDoc, type WorkflowRunDeliverable } from '../../shared/data/api'


export const ABSENT_COPY: Record<WorkflowDeliverableAbsence, string> = {
  template_unknown:
    'This template is not one of the five loop kinds, so nothing declares a document name for it. Unknown, not absent — a bespoke template may well produce a document under a name only it knows.',
  kind_has_no_document:
    'This kind produces no document — the passing check or the diff IS the output. Nothing is missing here.',
  not_written: 'The worker has not written it yet.',
  no_root: 'This run has no directory to read from — it was never launched, or retention has swept it.',
  unreadable: 'The file is there and could not be read. That is a permissions or disk problem, not a slow worker.',
}

export function absentCopy(doc: WorkflowDeliverableDoc): string {
  return doc.absent_reason ? ABSENT_COPY[doc.absent_reason] : 'Nothing to show.'
}

function DocSurface({ children }: { children: string }) {
  return (
    <div className="rounded-xl bg-surface px-2xl py-xl ring-1 ring-outline-variant/30">
      <Markdown>{children}</Markdown>
    </div>
  )
}

function Absent({ doc, instructed }: { doc: WorkflowDeliverableDoc; instructed: boolean | null }) {
  return (
    <div className="flex flex-col gap-xs rounded-xl bg-surface-high p-l">
      <p data-type="body-s" className="text-on-surface-low">{absentCopy(doc)}</p>
      {
}
      {doc.absent_reason === 'not_written' && instructed === false && (
        <p data-type="caption" className="text-on-surface-low">
          This run’s template never names <span className="font-mono">{doc.name}</span>, so nothing has
          asked the worker to write one. Waiting will not produce it.
        </p>
      )}
    </div>
  )
}

function slotLabel(prefix: string, doc: WorkflowDeliverableDoc): string {
  return `${prefix} · ${doc.name ?? '—'}`
}

export function DeliverablePanel({ runId }: { runId: string }) {
  const [data, setData] = useState<WorkflowRunDeliverable | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [slot, setSlot] = useState<'report' | 'log'>('report')

  const load = useCallback(() => {
    let live = true
    setLoading(true)
    setError(null)
    api
      .workflowRunDeliverable(runId)
      .then((body) => { if (live) setData(body) })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : 'could not read this run’s document') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  useEffect(() => load(), [load])

  if (loading) return <FormSkeleton sections={1} rows={3} title={false} />
  if (error) return <InlineError onRetry={load}>{error}</InlineError>
  if (!data) return null

  const doc = slot === 'report' ? data.report : data.log
  const declared = data.derivation.declared_by

  return (
    <section className="flex flex-col gap-s" aria-labelledby="run-deliverable-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-s">
        {
}
        <h2 id="run-deliverable-heading" data-type="label-l" className="flex items-center gap-xs text-on-surface-var">
          <FileText size={14} aria-hidden /> Document
        </h2>
        {
}
        {declared && (
          <p data-type="caption" className="text-on-surface-low">
            <span className="font-mono">{declared.name || '—'}</span>
            {' — declared by the '}
            <span className="font-mono">{declared.kind}</span>
            {declared.variant ? <> kind’s <span className="font-mono">{declared.variant}</span> variant</> : ' kind'}
          </p>
        )}
      </div>

      <Segmented
        ariaLabel="Document"
        value={slot}
        onChange={(v) => setSlot(v as 'report' | 'log')}
        options={[
          { key: 'report', label: slotLabel('Deliverable', data.report) },
          { key: 'log', label: slotLabel('Log', data.log) },
        ]}
      />

      {doc.present && doc.content !== null ? (
        <>
          <DocSurface>{doc.content}</DocSurface>
          <p data-type="caption" className="text-on-surface-low">
            {doc.bytes?.toLocaleString()} bytes, read from this run’s{' '}
            {doc.found_in === 'workspace' ? 'workspace' : 'run directory'}
            {doc.truncated && ' · truncated for display — open the file for the rest'}
            {
}
            {doc.clipped_blobs > 0 &&
              ` · ${doc.clipped_blobs} blob${doc.clipped_blobs === 1 ? '' : 's'} clipped (a run of 512+ characters with no whitespace is not prose)`}
          </p>
        </>
      ) : (
        <Absent doc={doc} instructed={data.instructed} />
      )}

      {
}
      {!doc.present && data.roots.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">
          Looked in: {data.roots.map((r) => `${r.path}${r.exists ? '' : ' (missing)'}`).join(', ')}
        </p>
      )}

      {
}
      {!doc.present && slot === 'report' && data.log.present && (
        <p data-type="caption" className="flex items-center gap-xs text-on-surface-low">
          <NotebookPen size={12} aria-hidden /> The working log has content — switch to Log.
        </p>
      )}
    </section>
  )
}
