import { useState } from 'react'
import { AnimatePresence } from 'framer-motion'
import { Check, X, ShieldCheck, Inbox, Sparkles, CheckCheck } from 'lucide-react'
import { api } from '../../../shared/data/api'
import { reportingWrite } from '../../../app/shell/reportingWrite'
import { rowSubject } from '../../../shared/data/rowSubject'
import { useDashboardLive } from '../DashboardLive'
import { SlotEmptyState, WidgetRow, RowAction } from './kit'
import { InlineError } from '../../../shared/ui/InlineError'
import { ListSkeleton } from '../../../shared/ui/ListScaffold'
import type { RouteProps } from '../../../app/shell/useQueryState'
import { invalidateKeys } from '../../../shared/data/data'

type Kind = 'approval' | 'inbox' | 'proposal'
interface Entry { key: string; kind: Kind; title: string; sub: string; id: string; session?: string; inboxItemId?: string }

function needsAction(item: { item_kind?: string; classification?: string }): boolean {
  return ['needs_input', 'agent_request', 'proposal', 'user_note'].includes(item.item_kind ?? '')
    || (['message', 'mention', 'email'].includes(item.item_kind ?? 'message') && item.classification === 'needs_reply')
}

export function ActionCenter({ navigate }: RouteProps) {
  const bustProposals = () => invalidateKeys('skill-proposals', true)
  const {
    approvals, inbox, proposals, refreshAll,
    approvalsErr, inboxErr, proposalsErr, retryApprovals, retryInbox, retryProposals, read,
  } = useDashboardLive()
  const [busy, setBusy] = useState<Set<string>>(new Set())
  const [done, setDone] = useState<Set<string>>(new Set())

  const withBusy = async (key: string, what: string, fn: () => Promise<unknown>) => {
    setBusy((s) => new Set(s).add(key))
    const ok = await reportingWrite(what, fn)
    if (ok) setDone((s) => new Set(s).add(key))
    setBusy((s) => { const n = new Set(s); n.delete(key); return n })
    refreshAll()
  }

  const proposalIds = new Set(proposals.map((p) => p.id))
  const approvalIds = new Set(approvals.map(approval => approval.id))
  const proposalInbox = new Map(inbox.flatMap(item => item.refs?.skill_proposal ? [[String(item.refs.skill_proposal), item.id] as const] : []))
  const liveInbox = inbox.filter(
    (i) => needsAction(i)
      && !(i.refs?.skill_proposal && proposalIds.has(String(i.refs.skill_proposal)))
      && !(i.refs?.approval && approvalIds.has(String(i.refs.approval))),
  )
  const allEntries: Entry[] = [
    ...approvals.map((a) => ({ key: `a:${a.id}`, kind: 'approval' as const, id: a.id, title: `Run ${a.tool}`, sub: a.tool_purpose || a.source || 'Tool approval', session: a.session })),
    ...liveInbox.map((i) => ({ key: `i:${i.id}`, kind: 'inbox' as const, id: i.id, title: i.sender_name || i.channel_name || 'Message', sub: i.message?.slice(0, 90) || '' })),
    ...proposals.map((p) => ({ key: `p:${p.id}`, kind: 'proposal' as const, id: p.id, title: `Skill: ${p.slug}`, sub: p.description?.slice(0, 90) || '', inboxItemId: proposalInbox.get(p.id) })),
  ].filter((e) => !done.has(e.key))

  const failures: { key: string; what: string; retry: () => void }[] = [
    ...(approvalsErr ? [{ key: 'approvals', what: 'pending approvals', retry: retryApprovals }] : []),
    ...(inboxErr ? [{ key: 'inbox', what: 'inbox items', retry: retryInbox }] : []),
    ...(proposalsErr ? [{ key: 'proposals', what: 'skill proposals', retry: retryProposals }] : []),
  ]

  if (allEntries.length === 0 && failures.length === 0) {
    if (!read.approvals || !read.inbox || !read.proposals) return <ListSkeleton rows={3} />
    return <SlotEmptyState icon={CheckCheck}>All clear — nothing waiting on you.</SlotEmptyState>
  }

  const CAP = 8
  const entries = allEntries.slice(0, CAP)
  const overflow = allEntries.length - entries.length

  const icon = { approval: ShieldCheck, inbox: Inbox, proposal: Sparkles }
  const tone = { approval: 'var(--color-warn)', inbox: 'var(--color-secondary)', proposal: 'var(--color-primary)' }
  const routeFor = (e: Entry) => {
    if (e.kind === 'approval' && e.session) return `chat/${encodeURIComponent(e.session)}`
    if (e.kind === 'approval') return 'chat'
    if (e.kind === 'inbox') return `inbox?open=${encodeURIComponent(e.id)}`
    if (e.inboxItemId) return `inbox?kind=proposal&open=${encodeURIComponent(e.inboxItemId)}`
    return 'skills?mode=proposals'
  }

  const primary = (e: Entry) => {
    if (e.kind === 'approval') withBusy(e.key, `approve “${rowSubject([e.title, e.sub])}”`, () => api.resolveApproval(e.id, 'approve'))
    else if (e.kind === 'proposal') withBusy(e.key, `accept “${rowSubject([e.title, e.sub])}”`, () => api.acceptSkillProposal(e.id).then(bustProposals))
    else navigate(routeFor(e))
  }
  const secondary = (e: Entry) => {
    if (e.kind === 'approval') withBusy(e.key, `reject “${rowSubject([e.title, e.sub])}”`, () => api.resolveApproval(e.id, 'reject'))
    else if (e.kind === 'proposal') withBusy(e.key, `reject “${rowSubject([e.title, e.sub])}”`, () => api.rejectSkillProposal(e.id).then(bustProposals))
    else withBusy(e.key, `dismiss “${rowSubject([e.title, e.sub])}”`, () => api.updateInboxItem(e.id, { status: 'dismissed' }))
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      <span data-type="caption" className="text-on-surface-low">{allEntries.length} item{allEntries.length === 1 ? '' : 's'} need your decision</span>
      {failures.map((f) => (
        <InlineError key={f.key} icon onRetry={f.retry}>
          Couldn&rsquo;t load {f.what}.
        </InlineError>
      ))}
      <AnimatePresence initial={false}>
        {entries.map((e) => {
          const Icon = icon[e.kind]
          const isBusy = busy.has(e.key)
          const subject = rowSubject([e.title, e.sub])
          return (
            <WidgetRow
              key={e.key}
              onClick={() => navigate(routeFor(e))}
              label={subject}
              actions={
                isBusy ? <span data-type="label-m" className="px-m text-on-surface-low">…</span> : (
                  e.kind === 'inbox' ? (
                    <>
                      <RowAction tone="primary" onClick={() => primary(e)} title="Open inbox item"
                        ariaLabel={`Open: ${subject}`}><Inbox size={14} /> Open</RowAction>
                      <RowAction tone="danger" onClick={() => secondary(e)} title="Dismiss"
                        ariaLabel={`Dismiss: ${subject}`}><X size={14} /></RowAction>
                    </>
                  ) : (
                    <>
                      <RowAction tone="ok" onClick={() => primary(e)} title={e.kind === 'approval' ? 'Approve' : 'Accept'}
                        ariaLabel={`${e.kind === 'approval' ? 'Approve' : 'Accept'}: ${subject}`}><Check size={14} /> {e.kind === 'approval' ? 'Approve' : 'Accept'}</RowAction>
                      <RowAction tone="danger" onClick={() => secondary(e)} title="Reject"
                        ariaLabel={`Reject: ${subject}`}><X size={14} /></RowAction>
                    </>
                  )
                )
              }
            >
              <div className="flex items-center gap-s">
                <Icon size={15} style={{ color: tone[e.kind] }} className="shrink-0" />
                <div className="min-w-0">
                  <p data-type="title-m" className="truncate text-on-surface">{e.title}</p>
                  {e.sub && <p data-type="body-m" className="truncate text-on-surface-low">{e.sub}</p>}
                </div>
              </div>
            </WidgetRow>
          )
        })}
      </AnimatePresence>
      {overflow > 0 && (
        <button
          type="button"
          onClick={() => navigate(proposals.length >= inbox.length ? 'skills?mode=proposals' : 'inbox')}
          className="mt-xs self-start rounded-pill px-m py-xs text-on-surface-low transition-colors hover:bg-surface-high hover:text-on-surface"
          data-type="label-m"
        >
          Showing {entries.length} of {allEntries.length} · Open the rest →
        </button>
      )}
    </div>
  )
}
