import { RefreshCw, Scissors } from 'lucide-react'
import { api, evaluationArmExecutions } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { LoadError, ListSkeleton } from '../../shared/ui/ListScaffold'
import { PageTitle } from '../../shared/ui/PageTitle'
import { QuietButton } from '../../shared/ui/QuietButton'
import { TopBar } from '../../shared/ui/TopBar'

export function EvalsPage() {
  const { data, error, refresh } = useQuery('evals:ablation', api.ablation, { persist: true })

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={
          <div className="flex items-center gap-s">
            <Scissors size={18} className="text-on-surface-var" />
            <PageTitle>Evaluations</PageTitle>
          </div>
        }
        right={
          <QuietButton title="Refresh" onClick={refresh}>
            <RefreshCw size={14} /> Refresh
          </QuietButton>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto flex flex-col gap-xl px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {data === undefined && error ? (
            <LoadError what="evaluation runs" error={error} onRetry={refresh} />
          ) : data === undefined ? (
            <ListSkeleton rows={3} what="evaluation arms" />
          ) : (
            <EvaluationArms view={data} />
          )}
        </div>
      </div>
    </div>
  )
}

function EvaluationArms({ view }: { view: Awaited<ReturnType<typeof api.ablation>> }) {
  const rows = evaluationArmExecutions(view.report)
  return (
    <section className="flex flex-col gap-m rounded-xl border border-outline-variant/30 bg-surface-container p-l" aria-labelledby="evaluation-arms-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-s">
        <h2 id="evaluation-arms-heading" data-type="title-m" className="text-on-surface">Evaluation arms</h2>
        <span className="text-on-surface-low text-[0.75rem]">{view.report.matrix_id}</span>
      </div>
      <p className="text-on-surface-low text-[0.8125rem]">
        Execution is confirmed by each cell&apos;s returned run output. A requested arm without
        returned evidence remains unattributed rather than being counted from its registered
        component shape.
      </p>
      <ul className="grid gap-s">
        {rows.map((row) => (
          <li key={row.arm || 'unattributed'} className="flex flex-wrap items-center justify-between gap-s rounded-lg bg-surface-high px-m py-s text-[0.8125rem]">
            <span className="text-on-surface">{row.arm || 'unattributed'}</span>
            <span className={row.executed ? 'text-on-surface-var' : 'text-warn'}>
              {row.executed ? `${row.scored_cells} of ${row.cells} scored` : `${row.cells} without execution evidence`}
              {row.verifier_absent > 0 ? ` · ${row.verifier_absent} verifier absent` : ''}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
