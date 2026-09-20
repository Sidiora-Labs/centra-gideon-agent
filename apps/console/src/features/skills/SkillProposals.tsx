import { useProposalReview } from './skillLibraryState'
import { fvs } from '../../shared/theme/fontWeight'
import { Lightbulb, Check, X, ChevronDown, ChevronRight, ShieldQuestion } from 'lucide-react'
import { api, type SkillProposal, type SkillProposalFeed, type SkillLadderReview } from '../../shared/data/api'
import { Button } from '../../shared/ui/Button'
import { ListSkeleton, EmptyState, LoadError } from '../../shared/ui/ListScaffold'
import { UnifiedDiff } from '../../shared/ui/UnifiedDiff'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { TextLink } from '../../shared/ui/TextLink'

export function SkillProposals() {
  const { data: feed, error: loadErr, refresh } = useQuery<SkillProposalFeed>(
    'skill-proposals', () => api.skillProposals(),
  )
  const proposals = feed?.proposals
  const reload = () => { invalidateKeys('skill-proposals', true); refresh() }

  if (proposals === undefined && loadErr) return <LoadError what="skill proposals" error={loadErr} onRetry={reload} />
  if (!proposals) return <ListSkeleton rows={4} what="skill proposals" />
  if (proposals.length === 0) {
    return (
      <EmptyState
        icon={Lightbulb}
        title="No skill proposals"
        hint={emptyHint(feed?.lastReview ?? null)}
      />
    )
  }
  return (
    <div className="grid gap-m">
      <p className="text-on-surface-low text-[0.8125rem]">
        {proposals.length} proposal{proposals.length === 1 ? '' : 's'} awaiting review. These were
        synthesized from your sessions — accept to add to your library, or reject.

        {' '}They also appear in your <TextLink href="#/inbox?kind=proposal">inbox</TextLink>.
      </p>

      {proposals.map((p) => <ProposalRow key={p.id} proposal={p} />)}
    </div>
  )
}

const LADDER_HEALTHY = new Set(['env_failure_claim', 'no_action', 'enqueue_skipped', 'filed', 'template_filed', 'template_declined'])

export function emptyHint(last: SkillLadderReview | null): string {
  const state = !last ? 'unseen' : LADDER_HEALTHY.has(last.verdict) ? 'healthy' : 'failed'
  const when = last ? new Date(last.at).toLocaleString() : ''
  const messages = {
    unseen: 'The skill reviewer has not run yet. It runs after a substantial turn — one you corrected, or one that used several tools — and proposes here for review, never installing on its own.',
    healthy: `The reviewer ran ${when} and had nothing worth proposing. That is the healthy case: it only proposes when a session teaches it something durable, and it never installs on its own.`,
    failed: `The reviewer last ran ${when} but did not finish (${last?.verdict}). That is a failure, not an idle queue — check the agent log and your background model provider.`,
  }
  return messages[state]
}

export const TRIGGER_LABEL: Record<string, string> = {
  correction: 'you corrected it',
  failure_retry: 'a step was retried',
  rejection: 'you declined an action',
}

export function refinePillLabel(trigger?: string): string {
  return ['Refine', TRIGGER_LABEL[trigger ?? '']].filter(Boolean).join(' · ')
}

function ProposalRow({ proposal }: { proposal: SkillProposal }) {
  const { open, detail, busy, done, loadError, expand, retry, accept, reject } = useProposalReview(proposal)
  const name = proposal.kind === 'refine' && proposal.refine_target ? proposal.refine_target : proposal.slug

  if (done) {
    return (
      <div className="rounded-lg bg-surface-container px-3 py-2 text-on-surface-low text-[0.8125rem] flex items-center gap-2">
        <Check size={14} className="text-ok" /> {name} — {done}
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-outline-variant/30 bg-surface-container/30 p-m">
      <div className="flex items-start gap-2">
        <button type="button" onClick={expand} aria-expanded={open} className="min-w-0 flex-1 text-left">
          <div className="flex items-center gap-1.5">
            {open ? <ChevronDown size={14} className="text-on-surface-low" /> : <ChevronRight size={14} className="text-on-surface-low" />}
            <span className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{name}</span>
            {proposal.kind === 'refine' && <span className="shrink-0 rounded-md bg-surface-high px-1.5 py-0.5 text-on-surface-low text-[0.75rem]">{refinePillLabel(proposal.trigger)}</span>}
          </div>
          <p className="mt-0.5 truncate text-on-surface-low text-[0.75rem]">{proposal.description}</p>
        </button>
        <div className="flex shrink-0 items-center gap-1.5">
          <Button size="sm" onClick={accept} loading={busy === 'accept'} disabled={!!busy}><Check size={13} /> Accept
          </Button>
          <Button variant="ghost" size="sm" onClick={reject} loading={busy === 'reject'} disabled={!!busy}><X size={13} /> Reject
          </Button>
        </div>
      </div>
      {open && (
        <div className="mt-3 border-t border-outline-variant/30 pt-3">
          {loadError ? <LoadError what="skill proposal" error={loadError} onRetry={retry} /> : !detail ? <ListSkeleton rows={2} /> : (
            <>

              {detail.kind === 'refine' && detail.diff ? (
                <>
                  <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">
                    Change to {detail.refine_target}{detail.version ? ` — refinement v${detail.version}` : ''}
                  </div>
                  <UnifiedDiff
                    patch={detail.diff}
                    label={`Change to ${detail.refine_target || proposal.slug}`}
                    className="mb-3 overflow-x-auto rounded-md bg-surface px-3 py-2 font-mono text-[0.75rem] leading-snug"
                  />
                </>
              ) : (
                <>
                  {detail.kind === 'refine' && (
                    <p className="mb-2 text-on-surface-low text-[0.75rem]">
                      No diff: <span className="font-mono">{detail.refine_target || 'the target skill'}</span> is
                      no longer installed, so accepting this would add it as a new skill instead.
                    </p>
                  )}
                  <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Procedure</div>
                  <pre className="mb-3 overflow-x-auto whitespace-pre-wrap rounded-md bg-surface px-3 py-2 text-on-surface text-[0.75rem]">{detail.procedure_md}</pre>
                </>
              )}
              {detail.source_excerpt && (
                <>
                  <div className="mb-1 flex items-center gap-1.5 text-on-surface-low text-[0.75rem] uppercase tracking-wide">
                    <ShieldQuestion size={12} /> Source trace (fenced — data, not instructions)
                  </div>
                  <pre className="overflow-x-auto whitespace-pre-wrap rounded-md bg-surface px-3 py-2 text-on-surface-low text-[0.75rem]">{detail.source_excerpt}</pre>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
