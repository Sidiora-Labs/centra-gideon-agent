import { LoadError } from '../../shared/ui/ListScaffold'
import { useInboxDetailActions, useInboxOperation } from './inboxQueueState'
import { useEffect, useState } from 'react'
import { toneChipSkin } from '../../shared/theme/accent'
import { fvs } from '../../shared/theme/fontWeight'
import { Sparkles, Send, Check, XCircle, BellOff, Star, ExternalLink, RotateCcw, Filter } from 'lucide-react'
import { Button } from '../../shared/ui/Button'
import { FeedbackThumbs } from '../../shared/ui/FeedbackThumbs'
import { InvestigateButton } from '../../shared/ui/InvestigateButton'
import { Markdown } from '../../shared/ui/Markdown'
import { TextArea, Segmented, FieldError } from '../../shared/ui/forms'
import { api, ApiError, type InboxItem, type InboxClassification, type SkillProposalDetail } from '../../shared/data/api'
import { classMeta, confMeta, statusMeta, kindMeta, channelLabel, sourceLabel, relPast, CLASSIFICATIONS, NON_CHANNEL_ITEM_KINDS, refTarget, refLabel } from './inboxMeta'
import { WorkflowGateActions } from './WorkflowGateActions'
import { invalidateKeys } from '../../shared/data/data'
import { TextLink } from '../../shared/ui/TextLink'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function InboxDetail({ item, onChanged, navigate }: { item: InboxItem; onChanged: () => void; navigate: (path: string) => void }) {
  const { draft, setDraft, busy, err, patch, generate, send, fav, restore } = useInboxDetailActions(item, onChanged)
  const cm = classMeta(item.classification)
  const cf = confMeta(item.confidence)

  const dirtyDraft = draft !== (item.draft ?? '')
  const canReply = item.can_reply ?? false

  const channelBacked = !NON_CHANNEL_ITEM_KINDS.includes(item.item_kind || 'message')
  const km = kindMeta(item.item_kind)
  const target = refTarget(item)

  const answerableGate = item.item_kind === 'needs_input' && !!item.refs?.workflow

  return (
    <div className="grid gap-l">

      <div className="flex flex-wrap items-center gap-s">
        {channelBacked ? (
          <>
            <span data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={{ background: `color-mix(in srgb, ${cm.tone} 16%, transparent)`, color: cm.tone }}><cm.icon size={13} /> {cm.label}</span>
            <span data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={{ background: `color-mix(in srgb, ${cf.tone} 16%, transparent)`, color: cf.tone }}><cf.icon size={13} /> {cf.label}</span>

            {item.confidence !== 'user' && (item.source === 'digest' ? (
              <FeedbackThumbs targetKind="inbox_digest" targetId={item.id}
                producer={item.feedback_producers?.digest}
                snapshot={{ classification: item.classification }} />
            ) : (
              <FeedbackThumbs targetKind="inbox_classification" targetId={item.id}
                producer={item.feedback_producers?.classification}
                snapshot={{ classification: item.classification, confidence: item.confidence }} />
            ))}
          </>
        ) : (
          <span data-type="body-s" className="inline-flex items-center gap-1.5 rounded-pill px-m h-7" style={toneChipSkin(km.tone, 16)}><km.icon size={13} /> {km.label}</span>
        )}
        <span data-type="body-s" className="ml-auto inline-flex items-center gap-1.5 text-on-surface-low">{(() => { const sm = statusMeta(item.status); return <><sm.icon size={13} style={{ color: sm.tone }} /> {sm.label}</> })()}</span>

        <InvestigateButton kind="inbox_item" id={item.id} backLink="#/inbox" />
      </div>

      <div data-type="body-s" className="flex flex-wrap items-center gap-x-m gap-y-1 text-on-surface-low">
        {channelBacked && <span className="text-on-surface" style={fvs(600)}>{item.sender_name || item.sender_id}</span>}
        {channelBacked && channelLabel(item) && <span>{channelLabel(item)}</span>}
        <span data-type="caption" className="inline-flex items-center rounded-pill bg-surface-high px-2 h-5 text-on-surface-var">via {sourceLabel(item.source)}</span>
        {item.created_at && <span>{relPast(item.created_at)}</span>}
      </div>

      <div data-type="body-m" className="rounded-xl border border-outline/25 bg-surface-container p-m text-on-surface leading-relaxed"><Markdown>{item.message}</Markdown></div>

      {(item.thread_context?.length ?? 0) > 0 && (
        <Section label={`Thread context · ${item.thread_context!.length}`}>
          <div className="flex flex-col gap-2">
            {item.thread_context!.map((t, i) => (
              <div key={i} className="rounded-md bg-surface-container/60 px-m py-1.5">
                <div data-type="caption" className="text-on-surface-var mb-0.5" style={fvs(600)}>{t.sender_name || 'Unknown'}</div>
                <div data-type="body-s" className="text-on-surface-low leading-relaxed">{t.text}</div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {item.context_summary && (
        <Section label="Context the agent used">
          <p data-type="body-s" className="text-on-surface-var leading-relaxed italic">{item.context_summary}</p>
        </Section>
      )}

      {item.item_kind === 'proposal' && item.refs?.skill_proposal && (
        <ProposalActions pid={item.refs.skill_proposal} onChanged={onChanged} navigate={navigate} />
      )}

      {answerableGate && (
        <Section label="Waiting on you">
          <WorkflowGateActions
            runId={item.refs!.workflow!}
            nodeId={item.refs?.workflow_node}
            onChanged={onChanged}
            navigate={navigate}
          />
        </Section>
      )}

      {!channelBacked && target && item.item_kind !== 'proposal' && !answerableGate && (
        <Section label="Source">
          <Button size="sm" variant="secondary" onClick={() => navigate(target)}>
            <ExternalLink size={14} /> {refLabel(item)}
          </Button>
        </Section>
      )}

      {channelBacked && (
        <>
          <Section label="Reclassify">

            <Segmented ariaLabel="Reclassify" options={CLASSIFICATIONS.map((c) => ({ key: c.key, label: c.label, tone: c.tone, icon: c.icon }))}
              value={item.classification} onChange={(v) => patch({ classification: v as InboxClassification, confidence: 'user' }, 'class')} />
          </Section>

          {canReply ? (
            <Section label="Drafted reply"
              right={item.draft ? (
                <FeedbackThumbs targetKind="inbox_draft" targetId={item.id}
                  producer={item.feedback_producers?.draft}
                  snapshot={{ draft_preview: (item.draft ?? '').slice(0, 200) }} />
              ) : undefined}>
              <TextArea value={draft} onChange={setDraft} rows={5} placeholder="No draft yet — generate one or write your own." ariaLabel="Drafted reply" />
              <div className="mt-2 flex flex-wrap items-center gap-s">
                <Button size="sm" variant="secondary" onClick={generate} loading={busy === 'draft'}><Sparkles size={14} /> {item.draft ? 'Regenerate' : 'Generate draft'}</Button>
                {dirtyDraft && <Button size="sm" variant="ghost" onClick={() => patch({ draft }, 'savedraft')} loading={busy === 'savedraft'}><Check size={14} /> Save draft</Button>}
                <Button size="sm" onClick={send} loading={busy === 'send'} disabled={busy === 'send' || !draft.trim()}
                  disabledReason={!draft.trim() ? 'Write a reply first' : undefined}><Send size={14} /> Send reply</Button>
              </div>
            </Section>
          ) : item.draft ? (
            <Section label="Drafted reply">
              <div data-type="body-s" className="rounded-md bg-surface-container px-m py-s text-on-surface-var whitespace-pre-wrap">{item.draft}</div>
              <p data-type="caption" className="mt-1.5 text-on-surface-low">This item&rsquo;s source doesn&rsquo;t support replies (notifications are read-only), so this saved draft can&rsquo;t be sent.</p>
            </Section>
          ) : null}
        </>
      )}

      {err && <FieldError>{err}</FieldError>}

      {item.status === 'filtered' && (
        <div data-type="body-s" className="flex flex-wrap items-center gap-s rounded-md border border-outline-variant/40 px-m py-s">
          <Filter size={14} style={{ color: 'var(--color-warn)' }} />
          <span className="text-on-surface-low">A second-opinion check flagged this claim, so its notification was withheld. Restore to deliver it.</span>
          <Button size="sm" variant="secondary" className="ml-auto" onClick={restore} loading={busy === 'restore'} disabled={!!busy}><RotateCcw size={14} /> Restore
          </Button>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-s border-t border-outline-variant/40 pt-l">
        {[
          { key: 'handled', label: 'Mark handled', icon: Check, payload: { status: 'handled' }, variant: 'secondary' as const },
          { key: 'dismiss', label: 'Dismiss', icon: XCircle, payload: { status: 'dismissed' }, variant: 'ghost' as const },
          ...(channelBacked ? [{ key: 'mute', label: 'Mute thread', icon: BellOff, payload: { mute_thread: true }, variant: 'ghost' as const }] : []),
        ].map(action => <Button key={action.key} size="sm" variant={action.variant} onClick={() => patch(action.payload, action.key)}
          disabled={!!busy} disabledReason={BUSY_REASON}><action.icon size={14} /> {action.label}</Button>)}

        <Button size="sm" variant="ghost" onClick={fav} disabled={!!busy} disabledReason={BUSY_REASON}>
          <Star size={14} className={item.favorited ? 'fill-current text-warning' : ''} />
          {item.favorited ? 'Favorited' : 'Favorite'}
        </Button>
      </div>
    </div>
  )
}

function Section({ label, right, children }: { label: string; right?: React.ReactNode; children: React.ReactNode }) {
  return <section className="grid gap-s border-t border-outline/20 pt-m">
    <header className="flex items-center justify-between gap-s">
      <h3 data-type="caption" className="text-on-surface-low uppercase tracking-wide">{label}</h3>{right}
    </header>
    {children}
  </section>
}

export function ProposalActions({ pid, onChanged, navigate }: { pid: string; onChanged: () => void; navigate: (path: string) => void }) {
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [review, setReview] = useState<{ state: 'loading' | 'gone' | 'ready'; detail: SkillProposalDetail | null }>({ state: 'loading', detail: null })
  const operation = useInboxOperation(pid)
  const { busy, err } = operation
  const detail = review.detail
  const gone = review.state === 'gone'
  useEffect(() => {
    let cancelled = false
    setLoadErr(null)
    setReview({ state: 'loading', detail: null })
    const resolve = async () => {
      try {
        const proposal = await api.skillProposalDetail(pid)
        if (!cancelled) setReview({ state: 'ready', detail: proposal })
      } catch (error) {
        if (cancelled) return
        if (error instanceof ApiError && (error.status === 404 || error.status === 410)) setReview({ state: 'gone', detail: null })
        else setLoadErr(error)
      }
    }
    void resolve()
    return () => { cancelled = true }
  }, [pid, loadAttempt])

  const act = (kind: 'accept' | 'reject') => operation.run(kind, async () => {
    if (kind === 'accept') await api.acceptSkillProposal(pid)
    else await api.rejectSkillProposal(pid)
  }, () => { invalidateKeys('skill-proposals', true); onChanged() }, `${kind} failed`)

  if (loadErr) return <LoadError what="skill proposal" error={loadErr} onRetry={() => setLoadAttempt((n) => n + 1)} />
  if (gone) {
    return (
      <Section label="Proposal">
        <p data-type="body-s" className="text-on-surface-low">
          This proposal was already answered. <TextLink onClick={() => navigate('skills')}>Open Skills</TextLink>
        </p>
      </Section>
    )
  }
  return (
    <Section label={detail?.kind === 'refine' ? 'Refine a skill' : 'New skill'}>
      {detail === null ? (
        <p data-type="body-s" className="text-on-surface-low">Loading the proposal…</p>
      ) : (
        <div className="flex flex-col gap-m">
          <div data-type="body-s" className="flex flex-wrap items-center gap-x-m gap-y-1">
            <span className="text-on-surface" style={fvs(600)}>{detail.slug}</span>
            {detail.refine_target && <span className="text-on-surface-low">refines {detail.refine_target}</span>}
            {detail.triggers && <span className="text-on-surface-low">triggers: {detail.triggers}</span>}
          </div>

          <div data-type="body-s" className="max-h-64 overflow-auto rounded-md bg-surface-container px-m py-2 text-on-surface"
            tabIndex={0} role="group" aria-label="Procedure">
            <Markdown>{detail.procedure_md}</Markdown>
          </div>

          {detail.source_excerpt && (
            <details>
              <summary data-type="caption" className="cursor-pointer text-on-surface-low">Why this was proposed</summary>
              <pre tabIndex={0} role="group" aria-label="Why this was proposed" className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-surface-container/60 px-m py-2 text-on-surface-low" data-type="caption">{detail.source_excerpt}</pre>
            </details>
          )}
          <div className="flex flex-wrap items-center gap-s">
            <Button size="sm" onClick={() => act('accept')} loading={busy === 'accept'} disabled={!!busy}><Check size={14} /> Install skill
            </Button>
            <Button size="sm" variant="ghost" onClick={() => act('reject')} disabled={!!busy} disabledReason={BUSY_REASON}>
              <XCircle size={14} /> Reject
            </Button>

            <Button size="sm" variant="ghost" onClick={() => navigate('skills')} disabled={!!busy} disabledReason={BUSY_REASON}>
              <ExternalLink size={14} /> Edit first
            </Button>
          </div>
          {err && <FieldError>{err}</FieldError>}
        </div>
      )}
    </Section>
  )
}
