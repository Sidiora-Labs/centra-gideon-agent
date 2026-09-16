import { LearningHeading, LearningTable, learningPanelClass, signedMeasurement } from './learningDisplay'
import { ArrowLeftRight, TrendingDown, TrendingUp } from 'lucide-react'
import { LoadError } from '../../shared/ui/ListScaffold'
import { StatusPill } from '../../shared/ui/StatusPill'
import { api, hasApiCode, type FieldMetricsRow } from '../../shared/data/api'
import { EvalsOff } from './EvalsOff'

export function FieldMetricsPanel({ rows, error, onRetry }: {
  rows: FieldMetricsRow[] | undefined
  error: unknown
  onRetry: () => void
}) {
  if (rows === undefined) {
    if (!error) return null
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className={learningPanelClass} aria-labelledby="field-metrics-heading">
          <Heading />
          <EvalsOff what="lab-vs-field table" />
        </section>
      )
    }
    return <LoadError what="lab vs field" error={error} onRetry={onRetry} />
  }
  if (!rows) return null

  return (
    <section className={learningPanelClass} aria-labelledby="field-metrics-heading">
      <Heading count={rows.filter((r) => r.lab_field_divergence).length} />
      {rows.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">
          No subjects yet. Once templates run or action types collect verdicts, each gets
          one row here: its lab score, its gate status, and what the field — your own
          thumbs, edits and approvals — says about it.
        </p>
      ) : (
        <>
          <p data-type="caption" className="text-on-surface-low">
            Lab is the newest pinned study result; gate is the newest Loop-2 report; field
            is derived from your 👍/👎, edit-before-approve and approval/undo records —
            computed per request, stored nowhere. A lab rise over a falling field trend is
            flagged and mechanically files an autonomy demotion.
          </p>
          <LearningTable caption="Lab results beside live field metrics, one row per subject" rows={rows} rowKey={row => `${row.subject_kind}:${row.subject}`} columns={[
            { name: 'Subject', render: row => <SubjectCell row={row} /> },
            { name: 'Lab', align: 'right', render: row => <LabCell lab={row.lab} /> },
            { name: 'Gate', render: row => <GateCell gate={row.gate} /> },
            { name: '👍/👎', align: 'right', render: row => row.field.thumb_rate === null ? '—' : `${pct(row.field.thumb_rate)} (${row.field.ups}/${row.field.ups + row.field.downs})` },
            { name: 'Edited before approve', align: 'right', render: row => row.field.edit_before_approve_rate === null ? '—' : `${pct(row.field.edit_before_approve_rate)} (${row.field.edited_runs}/${row.field.edited_runs + row.field.clean_approved_runs})` },
            { name: 'Approvals', align: 'right', render: row => row.field.approval_rate === null ? '—' : `${pct(row.field.approval_rate)}${row.field.undos ? ` · ${row.field.undos} undone` : ''}` },
            { name: 'Field trend', render: row => <TrendChip trend={row.field.trend} /> },
          ]} />
        </>
      )}
    </section>
  )
}

export const FIELD_METRICS_KEY = 'learning:field-metrics'
export const fetchFieldMetrics = () => api.evalFieldMetrics()

function Heading({ count = 0 }: { count?: number }) {
  return <LearningHeading id="field-metrics-heading" icon={ArrowLeftRight} suffix={count > 0 && <StatusPill tone="warn" className="gap-1.5 h-6 px-m w-fit"><TrendingDown size={12} /> {count} diverged</StatusPill>}>Lab vs field</LearningHeading>
}

function SubjectCell({ row }: { row: FieldMetricsRow }) {
  return <div className="flex flex-wrap items-center gap-s">
    <span className="break-all">{row.subject}</span>
    <span data-type="caption" className="rounded-md bg-surface-high px-s text-on-surface-low">{row.subject_kind === 'template' ? 'template' : 'action type'}</span>
    {row.lab_field_divergence && <StatusPill tone="warn" className="gap-1 h-5 px-m w-fit" title={row.divergence_reason}>lab_field_divergence</StatusPill>}
  </div>
}

function LabCell({ lab }: { lab: FieldMetricsRow['lab'] }) {
  if (lab === null || lab.score === null) return <span className="text-on-surface-low">not measured</span>
  const Direction = lab.rose === true ? TrendingUp : lab.rose === false ? TrendingDown : null
  return <span title={lab.model_fp ? `pinned ${lab.model_fp}` : undefined}>{lab.score.toFixed(3)}{Direction && <Direction size={12} className="ml-1 inline" aria-label={lab.rose ? 'rose' : 'fell'} />}</span>
}

function GateCell({ gate }: { gate: FieldMetricsRow['gate'] }) {
  const label = !gate ? 'no gate run' : gate.state !== 'gated' ? 'ungated' : 'gated'
  const delta = gate?.state === 'gated' && gate.delta !== null ? ` (${signedMeasurement(gate.delta, 3)})` : ''
  return <span className={label === 'gated' ? undefined : 'text-on-surface-low'}>{label}{delta}</span>
}

function TrendChip({ trend }: { trend: FieldMetricsRow['field']['trend'] }) {
  switch (trend) {
    case 'falling': return <StatusPill tone="warn" className="gap-1.5 h-6 px-m w-fit"><TrendingDown size={12} /> falling</StatusPill>
    case 'rising': return <span className="inline-flex items-center gap-1.5 text-on-surface-var"><TrendingUp size={12} /> rising</span>
    default: return <span className={trend ? 'text-on-surface-var' : 'text-on-surface-low'}>{trend ? 'flat' : 'too few signals'}</span>
  }
}

function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`
}
