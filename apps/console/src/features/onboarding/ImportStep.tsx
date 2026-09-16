import { useSetupImport } from './importSetupState'
import { motion } from 'framer-motion'
import { AlertTriangle, ArrowRight, Check, Loader2, ShieldCheck } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { TextLink } from '../../shared/ui/TextLink'
import { Checkbox } from '../../shared/ui/forms'
import { LoadError, LoadingStatus } from '../../shared/ui/ListScaffold'
import { listItemEnter, spring, stagger } from '../../shared/theme/motion'
import { fvs } from '../../shared/theme/fontWeight'
import {
  type OnboardingImportReport,
  type OnboardingImportSource,
} from '../../shared/data/api'

const CATEGORY_LABEL: Record<string, string> = {
  instructions: 'Instructions',
  memories: 'Memories',
  mcp_servers: 'MCP servers',
  skills: 'Skills',
  settings: 'Settings',
}

const CATEGORY_BLURB: Record<string, string> = {
  instructions: 'Your CLAUDE.md / AGENTS.md conventions, saved as memories.',
  memories: 'Notes the other tool was already remembering for you.',
  mcp_servers: 'MCP server definitions, added to your MCP config.',
  skills: 'Skills, copied in and re-scanned like a Store install.',
  settings: 'Staged for you to review — never merged into live config.',
}

export function labelOfCategory(category: string): string {
  return Object.prototype.hasOwnProperty.call(CATEGORY_LABEL, category) ? CATEGORY_LABEL[category] : category.split('_').join(' ')
}

export function summaryOfReport(report: OnboardingImportReport): string {
  const labels = { imported: 'imported', existing: 'already there', conflict: 'to review', rejected: 'refused' } as const
  return (Object.keys(labels) as Array<keyof typeof labels>).flatMap(outcome => report.counts[outcome] ? [`${report.counts[outcome]} ${labels[outcome]}`] : []).join(' · ') || 'Nothing to import'
}

export function ImportStep({ onDone, onSkip }: {

  onDone: (summary: string) => void

  onSkip: () => void
}) {
  const { scan, scanError, pickedSources, pickedCategories, busy, report, failure, detected,
    offered, tally, nothingPicked, load, run, pickSource, pickCategory } = useSetupImport()

  const announcement = report
    ? `Import finished: ${summaryOfReport(report)}.`
    : busy
      ? 'Importing your setup…'
      : scan === null
        ? ''
        : detected.length === 0
          ? 'No other agent tools were found on this machine.'
          : `Found ${detected.map((s) => s.display_name).join(' and ')}.`

  if (scan === null && scanError) {
    return <LoadError what="detected tools" error={scanError} onRetry={load} />
  }
  if (scan === null) {
    return (
      <div role="status" aria-busy="true" className="flex items-center py-2">
        <LoadingStatus what="detected tools" />
        <Loader2 size={18} className="animate-spin text-on-surface-low" aria-hidden="true" />
      </div>
    )
  }

  return (
    <div className="grid gap-l">
      <p role="status" aria-live="polite" className="sr-only">{announcement}</p>

      {report
        ? <Report report={report} onContinue={() => onDone(summaryOfReport(report))} />
        : detected.length === 0
          ? <Nothing looked={scan.sources} onContinue={() => onDone('Nothing to import')} />
          : (
            <>
              <p className="text-on-surface-var text-[0.8125rem]">
                We found another agent tool on this machine. Bring its setup over — it is
                read only over there, nothing is changed in the other tool, and credentials
                are never imported.
              </p>

              <motion.div className="flex flex-col gap-s" initial="initial" animate="animate"
                variants={{ animate: { transition: stagger(0.05) } }}>
                {detected.map((source) => (
                  <SourceCard key={source.source} source={source}
                    picked={!!pickedSources[source.source]}
                    onPick={(value) => pickSource(source.source, value)} />
                ))}
              </motion.div>

              <div className="flex flex-col gap-2">
                <span className="text-on-surface text-[0.8125rem]" style={fvs(550)}>
                  What to bring over
                </span>
                {offered.length === 0
                  ? <p className="text-on-surface-low text-[0.8125rem]">
                      Nothing to bring over from the tools you picked.
                    </p>
                  : offered.map((category) => {
                    const { total, existing } = tally(category)
                    return (
                      <CategoryRow key={category} category={category} total={total}
                        existing={existing} picked={!!pickedCategories[category]}
                        onPick={(value) => pickCategory(category, value)} />
                    )
                  })}
              </div>

              {failure && (
                <div className="flex flex-col gap-2">
                  <InlineError icon multiline>{failure}</InlineError>
                  <p className="text-on-surface-low text-[0.75rem]">
                    Whatever already landed was recorded, so importing again brings over only
                    what is still missing.
                  </p>
                </div>
              )}

              <div className="flex items-center gap-m">
                <Button variant="primary" size="md" loading={busy}
                  disabled={nothingPicked}
                  disabledReason="Pick a tool and at least one thing to bring over"
                  onClick={run}>
                  {failure ? 'Try again' : 'Import selected'}
                  <ArrowRight size={16} aria-hidden="true" />
                </Button>
                <TextLink onClick={onSkip}>Skip this</TextLink>
              </div>
            </>
          )}
    </div>
  )
}

function Nothing({ looked, onContinue }: {
  looked: OnboardingImportSource[]
  onContinue: () => void
}) {
  return (
    <div className="grid gap-l">
      <p className="text-on-surface-var text-[0.8125rem]">
        No other agent tools found on this machine. We looked for{' '}
        {looked.map((s) => s.display_name).join(' and ')} — if you install one later, you can
        import from it any time.
      </p>
      <div>
        <Button variant="primary" size="md" onClick={onContinue}>
          Continue <ArrowRight size={16} aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}

function SourceCard({ source, picked, onPick }: {
  source: OnboardingImportSource
  picked: boolean
  onPick: (v: boolean) => void
}) {
  const total = source.items.length
  return (
    <motion.div variants={listItemEnter} layout transition={spring.spatialFast}
      className="flex items-start gap-2 rounded-xl border border-outline/25 bg-surface-high p-m">
      <Checkbox checked={picked} onChange={onPick} className="mt-0.5"
        ariaLabel={`Import from ${source.display_name}`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-on-surface text-[0.875rem]" style={fvs(550)}>{source.display_name}</span>
          <span className="text-on-surface-low text-[0.75rem]">
            {total} {total === 1 ? 'thing' : 'things'} found
          </span>
        </div>
        <p className="mt-0.5 break-all font-mono text-on-surface-low text-[0.75rem]">{source.root}</p>
        {source.secrets_skipped > 0 && (
          <p className="mt-1 flex items-start gap-1.5 text-on-surface-var text-[0.75rem]">
            <ShieldCheck size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />

            {source.secrets_skipped} credential value{source.secrets_skipped === 1 ? '' : 's'} or file{source.secrets_skipped === 1 ? '' : 's'} will not be imported.
          </p>
        )}
      </div>
    </motion.div>
  )
}

function CategoryRow({ category, total, existing, picked, onPick }: {
  category: string
  total: number
  existing: number
  picked: boolean
  onPick: (v: boolean) => void
}) {
  const label = labelOfCategory(category)
  return (
    <div className="flex items-start gap-2">
      <Checkbox checked={picked} onChange={onPick} className="mt-0.5"
        ariaLabel={`Bring over ${label} (${total})`} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-on-surface text-[0.8125rem]">{label}</span>
          <span className="text-on-surface-low text-[0.75rem]">{total}</span>
          {existing > 0 && (
            <span className="text-[0.75rem]" style={{ color: 'var(--color-success)' }}>
              {existing} already imported
            </span>
          )}
        </div>
        <p className="text-on-surface-low text-[0.75rem]">
          {CATEGORY_BLURB[category] ?? `Imported as ${label.toLowerCase()}.`}
        </p>
      </div>
    </div>
  )
}

function Report({ report, onContinue }: {
  report: OnboardingImportReport
  onContinue: () => void
}) {
  const outcomes = report.results.reduce((groups, row) => {
    (groups[row.outcome] ??= []).push(row)
    return groups
  }, {} as Record<string, OnboardingImportReport['results']>)
  const imported = outcomes.imported ?? []
  const reviews = [{ key: 'conflict', title: 'Kept what you already had', tone: 'text-warn' }, { key: 'rejected', title: 'Refused for safety', tone: 'text-danger' }]
  return (
    <div className="grid gap-l">
      <p className="flex items-center gap-1.5 text-[0.875rem]" style={{ color: 'var(--color-success)' }}>
        <Check size={15} aria-hidden="true" /> {summaryOfReport(report)}
      </p>

      {imported.length > 0 && (
        <dl className="flex flex-col gap-1">
          {imported.map((r) => (
            <div key={r.fingerprint} className="flex gap-2 text-[0.75rem]">
              <dt className="w-[7rem] shrink-0 text-on-surface-low">{labelOfCategory(r.category)}</dt>
              <dd className="min-w-0 flex-1 break-words text-on-surface-var">
                {r.key}{r.destination ? ` → ${r.destination}` : ''}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {reviews.filter(review => outcomes[review.key]?.length).map(review =>
        <OutcomeList key={review.key} title={review.title} tone={review.tone} rows={outcomes[review.key]} />)}

      {report.notes.length > 0 && (
        <ul className="flex flex-col gap-1">
          {report.notes.map((note) => (
            <li key={note} className="flex items-start gap-1.5 text-on-surface-var text-[0.75rem]">
              <ShieldCheck size={13} aria-hidden="true" className="mt-0.5 shrink-0 text-ok" />
              {note}
            </li>
          ))}
        </ul>
      )}

      <div>
        <Button variant="primary" size="md" onClick={onContinue}>
          Continue <ArrowRight size={16} aria-hidden="true" />
        </Button>
      </div>
    </div>
  )
}

function OutcomeList({ title, tone, rows }: {
  title: string
  tone: string
  rows: OnboardingImportReport['results']
}) {
  return (
    <section role="group" className="flex flex-col gap-1.5" aria-label={title}>
      <span className={`flex items-center gap-1.5 text-[0.8125rem] ${tone}`} style={fvs(550)}>
        <AlertTriangle size={13} aria-hidden="true" /> {title}
      </span>
      <ul className="flex flex-col gap-1">
        {rows.map((r) => (
          <li key={r.fingerprint} className="text-[0.75rem]">
            <span className="text-on-surface">{labelOfCategory(r.category)} · {r.key}</span>
            {r.detail && <span className="text-on-surface-low"> — {r.detail}</span>}
          </li>
        ))}
      </ul>
    </section>
  )
}
