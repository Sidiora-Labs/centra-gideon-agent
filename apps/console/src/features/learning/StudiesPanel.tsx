import { LearningHeading, LearningTable, learningPanelClass } from './learningDisplay'
import { Fragment, useReducer } from 'react'
import { ChevronDown, ChevronRight, FlaskConical, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../shared/ui/ListScaffold'
import { QuietButton } from '../../shared/ui/QuietButton'
import { useQuery } from '../../shared/data/data'
import { api, hasApiCode, type StudyRow, type StudyView } from '../../shared/data/api'
import { EvalsOff } from './EvalsOff'
import { studyDetailKey } from './proposalCache'

export function StudiesPanel({ studies, error, onRetry }: {
  studies: StudyRow[] | undefined
  error: unknown
  onRetry: () => void
}) {
  const [open, toggleStudy] = useReducer((current: string, selected: string) => current === selected ? '' : selected, '')

  if (studies === undefined) {
    if (!error) return null
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className={learningPanelClass} aria-labelledby="studies-heading">
          <Heading />
          <EvalsOff what="study" />
        </section>
      )
    }
    return <LoadError what="studies" error={error} onRetry={onRetry} />
  }
  if (!studies) return null
  if (studies.length === 0) return <Empty />

  return (
    <section className={learningPanelClass} aria-labelledby="studies-heading">
      <Heading count={studies.length} />
      <p className="text-on-surface-low text-[0.75rem]">
        Each study is registered before its first run and judged against a rubric pinned by
        hash. Editing that rubric mid-study invalidates it rather than re-scoring it. Pairs are
        judged blind at both positions; below the agreement floor the study reports an
        unreliable judge instead of a winner.
      </p>
      <ul className="flex flex-col gap-xs">
        {studies.map(study => <StudyCard key={study.study_id} study={study} expanded={open === study.study_id} onToggle={() => toggleStudy(study.study_id)} />)}
      </ul>
    </section>
  )
}

function StudyCard({ study, expanded, onToggle }: { study: StudyRow; expanded: boolean; onToggle: () => void }) {
  const Disclosure = expanded ? ChevronDown : ChevronRight
  return <li className="overflow-hidden rounded-lg border border-outline-variant/30 bg-surface-container">
    <header className="flex flex-wrap items-start gap-m p-m">
      <div className="min-w-0 flex-1">
        <h3 data-type="title-s" className="truncate text-on-surface">{String(study.subject.template_id ?? study.study_id)}{versionRange(study)}</h3>
        {study.hypothesis && <p className="truncate text-on-surface-low text-[0.75rem]">{study.hypothesis}</p>}
        <p className="mt-xs text-on-surface-low text-[0.75rem]">k={study.k} · agreement {fmtRate(study.agreement)} (floor {fmtRate(study.agreement_floor)}) · win rate {fmtRate(study.win_rate)}</p>
      </div>
      <VerdictBadge study={study} />
      <QuietButton ariaExpanded={expanded} onClick={onToggle}><Disclosure size={13} aria-hidden="true" />{expanded ? 'Hide runs' : 'Show runs'}</QuietButton>
    </header>
    {expanded && <StudyDetail studyId={study.study_id} />}
  </li>
}

function StudyDetail({ studyId }: { studyId: string }) {
  const detail = useQuery<StudyView>(studyDetailKey(studyId), () => api.evalStudy(studyId))
  const view = detail.data
  if (!view) return <div className="px-l pb-m">{detail.error
    ? <LoadError what="study detail" error={detail.error} onRetry={detail.refresh} />
    : <p className="text-on-surface-low text-[0.75rem]">Loading the runs…</p>}</div>
  const judgement = view.verdict
  const pairs = view.runs.flatMap(run => run.pairs.map(pair => ({ caseId: run.case_id, pair })))
  const facts = [
    { label: 'Decision rule', content: view.decision_rule },
    { label: 'Rubric pinned at', content: view.rubric_sha256.slice(0, 16), mono: true },
    { label: 'Hidden locked checks', content: `${view.locked_check_count} — executed by the supervisor in each run's output workspace, never shown to a run` },
  ]

  return (
    <div className="flex flex-col gap-m border-t border-outline-variant px-l py-m">
      <dl className="grid grid-cols-[auto_1fr] gap-x-m gap-y-0.5 text-[0.75rem]">
        {facts.map(fact => <Fragment key={fact.label}>
          <dt className="text-on-surface-low">{fact.label}</dt>
          <dd className={`text-on-surface-var ${fact.mono ? 'font-mono' : ''}`}>{fact.content}</dd>
        </Fragment>)}
      </dl>

      {judgement === null
        ? (
          <p className="text-on-surface-low text-[0.8125rem]">
            Registered, not yet run. The design is sealed: k={view.k} over {view.inputs.length}{' '}
            case{view.inputs.length === 1 ? '' : 's'}.
          </p>
        )
        : <Tally verdict={judgement} />}

      {judgement?.detail && (
        <p className="text-on-surface-low text-[0.8125rem]">{judgement.detail}</p>
      )}

      {judgement && judgement.locked_regressions.length > 0 && (
        <div className="flex flex-col gap-0.5 rounded-md border border-error bg-surface-high px-m py-s">
          <span className="flex items-center gap-xs text-error text-[0.75rem]">
            <ShieldAlert size={13} aria-hidden="true" />
            Locked-check regression — a fail regardless of the win rate
          </span>
          <ul className="flex flex-col gap-0.5 text-error text-[0.75rem]">
            {judgement.locked_regressions.map((r) => <li key={r} className="font-mono">{r}</li>)}
          </ul>
        </div>
      )}

      {judgement && !judgement.ledger_row_written && (
        <p className="text-on-surface-low text-[0.75rem]">
          This verdict is not in <code className="text-on-surface-var">results.tsv</code>: the run
          could not be attributed to a model binding, and an unattributable score is refused
          rather than recorded.
        </p>
      )}

      {view.runs.length > 0 && (
        <div className="flex flex-col gap-xs">
          <span data-type="title-s" className="text-on-surface">Per-run pairs</span>
          <LearningTable caption={`Every judged pair for study ${view.study_id}, by case and trial`} rows={pairs} rowKey={row => `${row.caseId}-${row.pair.trial}`} columns={[
            { name: 'Case', render: row => row.caseId },
            { name: 'Trial', render: row => row.pair.trial },
            { name: 'Slot A held', render: row => row.pair.slot_a_arm },
            { name: 'A/B', render: row => row.pair.direct_winner },
            { name: 'B/A', render: row => row.pair.swapped_winner },
            { name: 'Counted as', render: row => row.pair.position_flipped ? <span className="text-on-surface-low">no signal — flipped with position</span> : row.pair.outcome },
          ]} />
          <p className="text-on-surface-low text-[0.75rem]">
            "Slot A held" is the randomized assignment, recorded outside the judge's prompt. A
            pair whose winner changes when the slots do is counted for neither arm.
          </p>
        </div>
      )}
    </div>
  )
}

function Tally({ verdict }: { verdict: NonNullable<StudyView['verdict']> }) {
  const tally = [`${verdict.wins} win`, `${verdict.losses} loss`, `${verdict.ties} tie`, `${verdict.no_signal} no signal`].join(' · ')
  const scope = `${verdict.decided_cases} decided case${verdict.decided_cases === 1 ? '' : 's'} at k=${verdict.k}`
  return <p className="text-on-surface-var text-[0.8125rem]">{tally}, over {scope}. Position-swap agreement {fmtRate(verdict.agreement)} against a {fmtRate(verdict.agreement_floor)} floor.{verdict.low_power && ' Low power: too few decided cases to be more than suggestive.'}</p>
}

function VerdictBadge({ study }: { study: StudyRow }) {
  const verdict = study.verdict
  const tone = verdict === 'win' ? 'text-success' : verdict === 'loss' ? 'text-error' : verdict === null ? 'text-on-surface-low' : 'text-on-surface-var'
  const label = verdict === null ? 'not run yet' : VERDICT_LABELS[verdict] ?? verdict
  return <span className={`shrink-0 rounded-md px-s py-0.5 text-[0.75rem] ${verdict === null ? '' : 'bg-surface-high'} ${tone}`}>{label}</span>
}

const VERDICT_LABELS: Record<string, string> = {
  win: 'candidate wins',
  loss: 'candidate loses',
  tie: 'no difference',
  invalidated: 'invalidated — rubric moved',
  judge_unreliable: 'judge unreliable',
}

function Empty() {
  return (
    <section className={learningPanelClass} aria-labelledby="studies-heading">
      <Heading />
      <p className="text-on-surface-low text-[0.8125rem]">
        No study has been registered. A study is the deliberate instrument for graduating a
        template change — k paired runs, a rubric pinned by hash, and hidden checks the runs
        cannot read. It is registered before it runs, on purpose, so its result cannot be
        re-interpreted afterwards.
      </p>
    </section>
  )
}

function Heading({ count }: { count?: number } = {}) {
  return <LearningHeading id="studies-heading" icon={FlaskConical} suffix={count !== undefined && <span className="text-on-surface-low text-[0.75rem]">{count}</span>}>Template studies</LearningHeading>
}

function versionRange(study: StudyRow): string {
  const versions = [study.subject.old_version, study.subject.new_version]
  return versions.some(version => version === undefined) ? '' : ` ${versions.map(version => `v${String(version)}`).join(' → ')}`
}

function fmtRate(value: number | null): string {
  return value === null ? 'not measured' : `${Math.round(value * 100)}%`
}
