import { AlertTriangle, ExternalLink, FileText, UserRound } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { LoadError } from '../../shared/ui/ListScaffold'
import { Segmented } from '../../shared/ui/Segmented'
import { fvs } from '../../shared/theme/fontWeight'
import type { IdentityReportView } from '../../shared/data/api'
import { useIdentityReportActions } from './learningActionState'
import { learningPanelClass } from './learningDisplay'

type Cadence = Exclude<IdentityReportView['cadence'], ''>

const CADENCE_OPTIONS: { key: Cadence; label: string }[] = [
  { key: 'monthly', label: 'Monthly' },
  { key: 'weekly', label: 'Weekly' },
  { key: 'off', label: 'Off' },
]

const CADENCE_LABEL = 'Write one automatically'

export function IdentityReportPanel({ report, error, onRetry, onDelivered }: {
  report: IdentityReportView | undefined
  error: unknown
  onRetry: () => void
  onDelivered: () => void
}) {
  const { busy, failure, slug, cadence, setCadenceTo, write } = useIdentityReportActions(report, onRetry, onDelivered)
  if (!report) return error ? <LoadError what="identity report" error={error} onRetry={onRetry} /> : null

  return (
    <section className={learningPanelClass} aria-labelledby="identity-report-heading">
      <div className="flex flex-wrap items-center gap-s">
        <UserRound size={16} className="text-on-surface-var" />
        <span id="identity-report-heading" data-type="title-m" className="text-on-surface">
          How I've adapted to you
        </span>
        <span className="text-on-surface-low text-[0.75rem]">
          last {report.window_days} days
        </span>
        <span className="flex-1" />

        <Button
          onClick={write}
          loading={busy} loadingLabel="Writing…"
          disabled={busy || report.total === 0}
          title="Compose the narrative, save it as a versioned artifact and send it to your inbox."
          disabledReason={report.total === 0
            ? 'Nothing has been learned yet, so there is nothing to write up.'
            : undefined}
        >
          <FileText size={14} /> Write it up
        </Button>
      </div>

      <div className="flex flex-wrap items-baseline gap-s">
        <span data-testid="cadence-label" className="text-on-surface-var text-[0.8125rem]">
          {CADENCE_LABEL}
        </span>
        {report.cadence === '' ? (

          <span className="text-warn text-[0.8125rem]">
            Your settings could not be read, so this cannot be shown or changed here.
          </span>
        ) : (
          <>
            <Segmented
              size="sm"
              ariaLabel={CADENCE_LABEL}
              value={cadence}
              options={CADENCE_OPTIONS}
              onChange={setCadenceTo}
            />
            <span className="text-on-surface-low text-[0.8125rem]">
              {cadence === 'off'
                ? 'Nothing is scheduled — use “Write it up” whenever you want one.'
                : 'Saved to your inbox on this cadence, with a versioned copy you can reread.'}
            </span>
          </>
        )}
      </div>

      {failure && (
        <p className="text-danger text-[0.8125rem]" role="alert">{failure}</p>
      )}
      {slug && (
        <p className="text-on-surface-low text-[0.8125rem]">
          Saved and sent to your inbox.{' '}
          <a className="text-primary underline" href={`#/artifacts/${encodeURIComponent(slug)}`}>
            Open the report <ExternalLink size={12} className="inline align-baseline" />
          </a>
        </p>
      )}

      {report.narrative && (
        <p className="rounded-lg bg-surface-container px-l py-l text-on-surface text-[0.9375rem]">
          {report.narrative}
        </p>
      )}
      {report.narrative_status === 'unavailable' && (
        <p
          className="inline-flex items-center gap-1.5 text-warn text-[0.8125rem]"
          title="The figures are gathered without a model. Only the prose needs one."
        >
          <AlertTriangle size={12} />
          No model was available to summarise this period — the figures below are unaffected.
        </p>
      )}

      <div className="grid gap-m" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(14rem, 1fr))' }}>
        {[
          { title: 'Preferences I hold', count: report.facets.count, lines: report.facets.items.map(item => `${item.text} — ${item.cls}, ${item.state}`) },
          { title: 'Lessons I follow', count: report.lessons.count, lines: report.lessons.items.map(item => item.text) },
          { title: 'Skills I built', count: report.skills.count, lines: report.skills.items.map(item => `${item.name} — ${item.uses} use${item.uses === 1 ? '' : 's'}, ${item.aging_state}`) },
          { title: 'Waiting on you', count: report.proposals.count, lines: report.proposals.items.map(item => item.label) },
        ].map(group => <Group key={group.title} {...group} shown={group.lines.length} />)}
      </div>
    </section>
  )
}

function Group({ title, count, shown, lines }: { title: string; count: number; shown: number; lines: string[] }) {
  const sample = lines.length > 0
  return <section className="flex min-w-0 flex-col gap-s rounded-xl border border-outline-variant/30 bg-surface-container p-l">
    <header className="flex items-baseline gap-s"><span data-type="title-s" className="text-on-surface" style={fvs(600)}>{count}</span><h3 className="text-on-surface-var text-[0.8125rem]">{title}</h3></header>
    {sample ? <ul className="flex flex-col gap-xs">{lines.map((line, index) => <li key={index} title={line} className="truncate text-on-surface-low text-[0.75rem]">{line}</li>)}</ul> : <p className="text-on-surface-low text-[0.75rem]">Nothing recorded yet.</p>}
    {shown < count && <p className="text-on-surface-low text-[0.6875rem]">Showing {shown} of {count}.</p>}
  </section>
}
