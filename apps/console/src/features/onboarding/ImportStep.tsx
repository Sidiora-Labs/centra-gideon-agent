import { useState } from 'react'
import { useSetupImport, writableImportItem } from './importSetupState'
import { motion } from 'framer-motion'
import { AlertTriangle, ArrowRight, Check, Loader2, ShieldCheck } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { TextLink } from '../../shared/ui/TextLink'
import { LoadError, LoadingStatus } from '../../shared/ui/ListScaffold'
import { stagger } from '../../shared/theme/motion'
import { fvs } from '../../shared/theme/fontWeight'
import {
  type OnboardingImportItem,
  type OnboardingImportReport,
  type OnboardingImportSource,
} from '../../shared/data/api'

const CATEGORY_LABEL: Record<string, string> = {
  instructions: 'Instructions',
  memories: 'Memories',
  mcp_servers: 'MCP servers',
  skills: 'Skills',
  settings: 'Settings',
  conversations: 'Conversations',
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
  const { scan, scanError, pickedItems, busy, report, failure, detected,
    nothingPicked, load, run, pickItems } = useSetupImport()

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
                    picked={pickedItems}
                    onPick={pickItems} />
                ))}
              </motion.div>


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
                  disabledReason="Pick at least one individual item to bring over"
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

function PickGroup({ items, picked, onPick, label }: {
  items: OnboardingImportItem[]; picked: Record<string, boolean>
  onPick: (items: OnboardingImportItem[], value: boolean) => void; label: string
}) {
  const eligible = items.filter(writableImportItem)
  const chosen = eligible.filter(item => picked[item.fingerprint]).length
  return <input type="checkbox" aria-label={label} checked={!!eligible.length && chosen === eligible.length}
    disabled={!eligible.length} ref={element => { if (element) element.indeterminate = chosen > 0 && chosen < eligible.length }}
    onChange={event => onPick(eligible, event.target.checked)} />
}

function ImportGroup({ category, items, picked, onPick }: {
  category: string; items: OnboardingImportItem[]; picked: Record<string, boolean>
  onPick: (items: OnboardingImportItem[], value: boolean) => void
}) {
  const [filter, setFilter] = useState('')
  const [page, setPage] = useState(0)
  const matching = items.filter(item => `${item.title} ${item.key} ${item.detail ?? ''}`.toLowerCase().includes(filter.toLowerCase()))
  const pages = Math.max(1, Math.ceil(matching.length / 40))
  const shownPage = Math.min(page, pages - 1)
  return <div className="grid gap-s">
    <div className="flex items-center gap-s"><PickGroup items={items} picked={picked} onPick={onPick}
      label={`Bring over ${labelOfCategory(category)} (${items.length})`} />
      <details className="min-w-0 flex-1"><summary>{labelOfCategory(category)} · {items.length} items</summary>
        <div className="grid gap-s py-s">
          <label>Filter {labelOfCategory(category)}<input type="search" className="w-full rounded-lg border border-outline-var bg-surface p-s"
            value={filter} onChange={event => { setFilter(event.target.value); setPage(0) }} /></label>
          {matching.slice(shownPage * 40, (shownPage + 1) * 40).map(item => <label key={item.fingerprint} className="flex items-start gap-s text-[0.8125rem]">
            <input type="checkbox" aria-label={`Import ${item.title}`} checked={!!picked[item.fingerprint] && writableImportItem(item)}
              disabled={!writableImportItem(item)} onChange={event => onPick([item], event.target.checked)} />
            <span className="min-w-0 break-words">{item.title}<span className="block text-on-surface-low">{item.state ?? (item.existing ? 'existing' : 'new')}{item.destination ? ` → ${item.destination}` : ''}{item.detail ? ` · ${item.detail}` : ''}</span>
              {!!((item.secrets_skipped ?? 0) + item.redactions) && <span className="block">{(item.secrets_skipped ?? 0) + item.redactions} credential values withheld</span>}</span>
          </label>)}
          {!matching.length && <p>No matching items</p>}
          {pages > 1 && <div className="flex items-center gap-s"><Button size="sm" variant="secondary" disabled={!shownPage} onClick={() => setPage(shownPage - 1)}>Previous</Button><span>Page {shownPage + 1} of {pages}</span><Button size="sm" variant="secondary" disabled={shownPage + 1 >= pages} onClick={() => setPage(shownPage + 1)}>Next</Button></div>}
        </div>
      </details>
    </div>
  </div>
}

function SourceCard({ source, picked, onPick }: {
  source: OnboardingImportSource; picked: Record<string, boolean>
  onPick: (items: OnboardingImportItem[], value: boolean) => void
}) {
  const categories = [...new Set(source.items.map(item => item.category))]
  return <section className="grid gap-s rounded-xl border border-outline/25 bg-surface-high p-m">
    <div className="flex items-start gap-s"><PickGroup items={source.items} picked={picked} onPick={onPick} label={`Import from ${source.display_name}`} />
      <div><span style={fvs(550)}>{source.display_name}</span><span className="ml-2 text-on-surface-low">{source.items.length} things found</span>
        <p className="break-all text-[0.75rem] text-on-surface-low">{source.root}</p>
        {!!source.secrets_skipped && <p>{source.secrets_skipped} credential value{source.secrets_skipped === 1 ? '' : 's'} or file{source.secrets_skipped === 1 ? '' : 's'} will not be imported.</p>}
      </div>
    </div>
    {categories.map(category => <ImportGroup key={category} category={category} items={source.items.filter(item => item.category === category)} picked={picked} onPick={onPick} />)}
    {source.not_imported?.map(row => <p key={row.what}>{row.what}: {row.count} not imported · {row.why}</p>)}
  </section>
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
  const reviews = [{ key: 'existing', title: 'Already here', tone: 'text-on-surface-low' }, { key: 'conflict', title: 'Kept what you already had', tone: 'text-warn' }, { key: 'rejected', title: 'Refused for safety', tone: 'text-danger' }]
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

      {!!report.unselected?.length && <p>{report.unselected.length} items left unselected.</p>}
      {report.missing?.map(fingerprint => <p key={fingerprint}>Selected item {fingerprint} is missing; preview again.</p>)}

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
