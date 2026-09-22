import { AlertTriangle, Brain, Check, RefreshCw, TrendingDown, X } from 'lucide-react'
import { TopBar } from '../../shared/ui/TopBar'
import { StatusPill } from '../../shared/ui/StatusPill'
import { Button } from '../../shared/ui/Button'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Segmented } from '../../shared/ui/forms'
import { InlineError } from '../../shared/ui/InlineError'
import { EmptyState, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { api, type LearningRow, type StagingWeek } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { AblationPanel } from './AblationPanel'
import { AttentionPanel } from './AttentionPanel'
import { FieldMetricsPanel } from './FieldMetricsPanel'
import { BenchmarkPanel } from './BenchmarkPanel'
import { HealthPanel } from './HealthPanel'
import { IdentityReportPanel } from './IdentityReportPanel'
import { JudgeBenchPanel } from './JudgeBenchPanel'
import { RetrievalBenchPanel } from './RetrievalBenchPanel'
import { StudiesPanel } from './StudiesPanel'
import { fvs } from '../../shared/theme/fontWeight'
import {
  DAY_HINT, DAY_TONE, bulkBlockedReason, dayLabel, dayState, evidenceLabel,
  gateLabel, gateRegressed, kindIcon, kindLabel, replayLabel, replayRegressed,
  tierLabel, tierTone,
} from './learningMeta'
import { PageTitle } from '../../shared/ui/PageTitle'
import { useLearningPage } from './learningPageState'
import { learningPanelClass } from './learningDisplay'
import { BUSY_REASON } from '../../shared/ui/unavailable'
import { useHashRoute } from '../../app/shell/useHashRoute'

export function LearningPage() {
  const page = useLearningPage()
  const { state, facets: kindChips, decide, setKind } = page
  const kind = state.kind
  const err = state.error
  const { data: inbox, loading, error: inboxError, refresh: refreshProposals } = page.proposals
  const pendingSkillProposals = useQuery(
    'learning:pending-skill-proposal-count',
    () => api.pendingSkillProposalCount(),
    { persist: false },
  )
  const rows = inbox?.rows ?? []
  const reports = [
    <IdentityReportPanel key="identity" report={page.identity.data} error={page.identity.error} onRetry={page.identity.refresh} onDelivered={page.identity.refresh} />,
    <HealthPanel key="health" health={page.health.data} error={page.health.error} onRetry={page.health.refresh} />,
    <AttentionPanel key="attention" scopes={page.attention.data?.scopes} error={page.attention.error} onRetry={page.attention.refresh} />,
    <FieldMetricsPanel key="field" rows={page.field.data?.subjects} error={page.field.error} onRetry={page.field.refresh} />,
    <JudgeBenchPanel key="judge" bench={page.judge.data} error={page.judge.error} onRetry={page.judge.refresh} />,
    <StudiesPanel key="studies" studies={page.studies.data?.studies} error={page.studies.error} onRetry={page.studies.refresh} />,
    <RetrievalBenchPanel key="retrieval" bench={page.retrieval.data} error={page.retrieval.error} onRetry={page.retrieval.refresh} />,
    <AblationPanel key="ablation" view={page.ablation.data} error={page.ablation.error} onRetry={page.ablation.refresh} />,
    <BenchmarkPanel key="benchmark" view={page.benchmark.data} error={page.benchmark.error} onRetry={page.benchmark.refresh} />,
  ]

  return (
    <div className="flex h-full flex-col">
      <TopBar
        left={
          <div className="flex items-center gap-s">
            <Brain size={18} className="text-on-surface-var" />
            <PageTitle>Learning</PageTitle>
          </div>
        }
        right={
          <QuietButton
            title="Refresh"
            onClick={() => { page.refresh(); pendingSkillProposals.refresh() }}
          >
            <RefreshCw size={14} /> Refresh
          </QuietButton>
        }
      />

      <div tabIndex={0} role="group" aria-label="Capture and proposals" className="flex-1 overflow-y-auto focus-visible:-outline-offset-2">
        <div className="mx-auto flex flex-col gap-xl px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
          {err && <InlineError icon onDismiss={page.clearError}>{err}</InlineError>}

          {page.week.data === undefined && page.week.error
            ? <LoadError what="capture week" error={page.week.error} onRetry={page.week.refresh} />
            : page.week.data && <WeekPanel week={page.week.data} />}
          {reports}

          <div className={learningPanelClass}>
            <div className="flex flex-wrap items-center gap-s">
              <span data-type="title-m" className="text-on-surface">Proposals</span>
              {!!inbox?.flagged && (
                <span
                  className="inline-flex items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
                  style={{ background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)', color: 'var(--color-warn)' }}
                  title="These carry an invalid change manifest. They are shown, not dropped — hiding them would bury a proposer bug."
                >
                  <AlertTriangle size={12} /> {inbox.flagged} flagged
                </span>
              )}
            </div>
            {kindChips.length > 1 && (
              <Segmented
                ariaLabel="Proposal kind"
                options={kindChips.map((c) => ({ key: c.key, label: c.label }))}
                value={kind}
                onChange={setKind}
              />
            )}

            {inbox === undefined && inboxError ? (
              <LoadError what="proposals" error={inboxError} onRetry={refreshProposals} />
            ) : loading && !inbox ? (
              <ListSkeleton rows={4} what="proposals" />
            ) : rows.length === 0 ? (
              <LearningEmptyState
                pendingSkillProposals={pendingSkillProposals.data}
                pendingSkillProposalsError={pendingSkillProposals.error}
              />
            ) : (
              <div className="flex flex-col gap-s">
                {rows.map((row) => (
                  <ProposalRow
                    key={row.id}
                    row={row}
                    busy={state.pending.has(row.id)}
                    onAccept={() => decide(row, 'accept')}
                    onReject={() => decide(row, 'reject')}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export function LearningEmptyState({ pendingSkillProposals, pendingSkillProposalsError }: {
  pendingSkillProposals: number | undefined
  pendingSkillProposalsError?: unknown
}) {
  const { navigate } = useHashRoute('learning')
  if (pendingSkillProposals === undefined && !pendingSkillProposalsError) {
    return <ListSkeleton rows={1} what="proposal queues" />
  }
  if (pendingSkillProposals && pendingSkillProposals > 0) {
    const noun = pendingSkillProposals === 1 ? 'proposal' : 'proposals'
    return <EmptyState
      icon={Brain}
      title={`${pendingSkillProposals} skill ${noun} await${pendingSkillProposals === 1 ? 's' : ''} review`}
      hint="This learning queue is empty, but the skill proposal queue still needs your review. Nothing is installed without your accept."
      action={{
        label: 'Review skill proposals',
        onClick: () => navigate('skills?mode=proposals'),
      }}
    />
  }
  if (pendingSkillProposalsError) {
    return <EmptyState
      icon={Brain}
      title="No proposals in this learning queue"
      hint="Skill proposals could not be checked, so there may still be proposals awaiting review. Nothing is installed without your accept."
      action={{
        label: 'Open skill proposals',
        onClick: () => navigate('skills?mode=proposals'),
      }}
    />
  }
  return <EmptyState
    icon={Brain}
    title="Nothing to review"
    hint="Proposals appear here when the system notices a pattern worth offering. Nothing is ever installed without your accept."
  />
}

function ProposalRow({ row, busy, onAccept, onReject }: {
  row: LearningRow; busy: boolean; onAccept: () => void; onReject: () => void
}) {
  const Icon = kindIcon(row.kind)
  const blocked = bulkBlockedReason(row)
  const facts = [
    row.provenance ? `from ${row.provenance}` : 'source unknown — cannot be weighed',
    row.source_cadence,
    row.reinforcements > 1 ? `seen ${row.reinforcements}×` : '',
    evidenceLabel(row),
  ].filter(Boolean).join(' · ')
  const regressions = [
    { visible: gateRegressed(row), label: 'score drop', detail: gateLabel(row) },
    { visible: replayRegressed(row), label: 'replay drop', detail: replayLabel(row) },
  ]
  return <article className="grid gap-m rounded-xl border border-outline-variant/30 bg-surface-container p-l">
    <header className="flex flex-wrap items-center gap-s">
      <Icon size={18} className="shrink-0 text-on-surface-var" />
      <h3 data-type="title-s" className="min-w-0 flex-1 break-words text-on-surface">{row.title || <span className="text-on-surface-low">(untitled — proposer bug)</span>}</h3>
      <span className="rounded-md bg-surface-high px-m py-xs text-[0.75rem]" style={{ color: tierTone(row.risk_tier) }}>{tierLabel(row.risk_tier)}</span>
      <span className="rounded-md bg-surface-high px-m py-xs text-on-surface-var text-[0.75rem]">{kindLabel(row.kind)}</span>
      {!row.manifest_valid && <StatusPill tone="warn" className="gap-xs" title={row.manifest_issues.join('; ')}><AlertTriangle size={12} /> manifest</StatusPill>}
      {regressions.filter(item => item.visible).map(item => <StatusPill key={item.label} tone="danger" className="gap-xs" title={item.detail}><TrendingDown size={12} /> {item.label}</StatusPill>)}
    </header>
    <div className="flex flex-col gap-xs text-[0.8125rem]">
      <p className="text-on-surface-var">{facts}</p>
      <p className="text-on-surface-low">{gateLabel(row)}{row.gate?.pin?.model_fp ? ` · pinned ${row.gate.pin.model_fp}` : ''}</p>
      <p className="text-on-surface-low">{replayLabel(row)}</p>
    </div>
    {row.source_excerpt && <blockquote className="rounded-lg border-l-2 border-outline-variant bg-surface-high p-m text-on-surface-var text-[0.75rem] break-words">{row.source_excerpt}</blockquote>}
    <footer className="flex flex-wrap items-center justify-between gap-m">
      <div className="min-w-0 flex-1">{blocked && <p className="text-on-surface-low text-[0.75rem]">Not bulk-acceptable: {blocked}</p>}</div>
      <div className="flex items-center gap-s">
        <Button size="sm" onClick={onAccept} disabled={busy} disabledReason={BUSY_REASON}><Check size={14} /> Accept</Button>
        <Button size="sm" variant="ghost" onClick={onReject} disabled={busy} disabledReason={BUSY_REASON}><X size={14} /> Reject</Button>
      </div>
    </footer>
  </article>
}

function WeekPanel({ week }: { week: StagingWeek }) {
  const firstPassDay = week.first_pass_day ?? null
  const days = week.buckets.map(day => {
    const state = dayState(day, firstPassDay)
    return {
      key: day.day, label: dayLabel(day.day), tone: DAY_TONE[state], passes: day.passes === 0 ? '—' : day.passes,
      title: `${day.day} — ${DAY_HINT[state]} (${day.passes} pass${day.passes === 1 ? '' : 'es'}, ${day.produced} produced, ${day.errors} error${day.errors === 1 ? '' : 's'})`,
      outcome: state === 'not_started' ? 'not started' : state === 'silent' ? 'silent' : day.produced > 0 ? `${day.produced} filed` : state === 'error' ? 'error' : 'ok',
    }
  })
  const footer = [
    `${week.produced_total} proposal${week.produced_total === 1 ? '' : 's'} filed`,
    week.cost_usd > 0 ? `$${week.cost_usd.toFixed(4)} spent` : '',
    week.error_days.length > 0 ? `errors on ${week.error_days.join(', ')}` : '',
  ].filter(Boolean).join(' · ')
  return <section className={learningPanelClass}>
    <header className="flex flex-wrap items-center gap-s">
      <h2 data-type="title-m" className="text-on-surface">Capture, last {week.days} days</h2>
      {week.silent_days.length > 0 && <StatusPill tone="warn" className="gap-xs" title="No capture pass ran on these days. An aggregate view cannot distinguish this from a quiet day."><AlertTriangle size={12} /> {week.silent_days.length} silent</StatusPill>}
    </header>
    <div className="grid grid-cols-[repeat(auto-fit,minmax(4.5rem,1fr))] gap-s">{days.map(day => <div key={day.key} title={day.title} className="flex flex-col items-center gap-xs rounded-lg border border-outline-variant/20 bg-surface-container p-m">
      <span className="text-on-surface-low text-[0.75rem]">{day.label}</span>
      <span data-type="title-s" style={{ ...fvs(600), color: day.tone }}>{day.passes}</span>
      <span className="text-on-surface-low text-[0.6875rem]">{day.outcome}</span>
    </div>)}</div>
    <p className="text-on-surface-low text-[0.75rem]">{footer}</p>
  </section>
}
