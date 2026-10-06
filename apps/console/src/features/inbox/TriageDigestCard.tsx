import { AlertTriangle, CheckCheck, Clock, ExternalLink, Inbox, Power, RotateCcw, ScrollText, Sparkles } from 'lucide-react'
import { type TriagePending, type TriageDigestView, type TriageDigestNotice, type TriageNoticeOutcome } from '../../shared/data/api'
import { useTriageDigest } from './triageDigestState'
import { Surface } from '../../shared/ui/Surface'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { TextInput } from '../../shared/ui/forms'
import { fvs } from '../../shared/theme/fontWeight'
import { TextLink } from '../../shared/ui/TextLink'
import { CommitmentDecisionsCard } from './CommitmentDecisionsCard'

export function TriageDigestCard() {
  const digest = useTriageDigest()
  const { view, error, refresh, busy, help, reply, undo } = digest
  const failedRead = (error && view === undefined) || view?.state === 'error'
  if (failedRead) return <Surface tone="container" radius="xl" className="mb-l border border-outline/30 p-l shadow-sm">
    <Header title="Morning triage" />
    <InlineError icon onRetry={refresh}>Couldn't read your digest: {view?.state === 'error' ? view.error : String((error as Error)?.message || error)}</InlineError>
  </Surface>
  if (!view) return null
  if (['uninstalled', 'off', 'never_run'].includes(view.state)) return <>
    <DigestSetup view={view} digest={digest} />
    <CommitmentDecisionsCard decisions={view.commitment_decisions || []} onRefresh={refresh} />
  </>

  const autoDone = view.auto_done || []
  const pending = view.pending || []
  // What the digest was about to do on its own and did not: it failed, or a safety rule or its
  // limit held it. Each says so under Needs you; this section must not read as if all went well.
  const notDone = pending.filter((row) => row.not_done).length
  const ran = view.ran || []
  const waiting = view.waiting || []
  const ledger = view.journal || []
  // What an earlier digest left waiting on you comes back here until you answer it, for a week; one
  // that waited longer is named below rather than simply gone. The rule is said once, beside them.
  const carried = pending.some((row) => row.carried_over)
  const noLonger = view.no_longer_offered || []

  return (
    <Surface tone="container" radius="xl" className="mb-l border border-outline/30 p-l shadow-sm">
      <Header
        title={view.title || 'Morning triage'}
        trailing={
          <div className="flex items-center gap-s">
            {view.degraded && <Badge tone="warn">Degraded</Badge>}
            {view.permalink && (
              <TextLink href={view.permalink} ink="emphasis" size="xs" icon={ExternalLink} iconPosition="trailing" iconSize={12}>
                Run journal
              </TextLink>
            )}
          </div>
        }
      />
      <p data-type="caption" className="mt-1 text-on-surface-low">
        {view.collected ?? 0} item{(view.collected ?? 0) === 1 ? '' : 's'} in this window
        {view.window_start ? <> since {view.window_start.slice(0, 16).replace('T', ' ')}</> : null}
        {view.dropped ? <> · {view.dropped} filtered by your rules</> : null}
      </p>

      {/* Why there may be no notification for a digest that plainly exists. Rendered from what your
          settings make of the digest's notice (`view.notice`, the server asking the same rule
          layer `notify()` delivers by), not from a delivery flag: the run cannot know what the
          gate did (see `handed_to_notify`). A fixed "held back" sentence was false for a badge or
          digest rule, which put the digest in the bell or kept it for the notification digest. */}
      <CommitmentDecisionsCard decisions={view.commitment_decisions || []} onRefresh={refresh} />
      <NoticeLine notice={view.notice} />

      {/* ── What your machine did ── */}
      <SectionHead icon={CheckCheck} title="What your machine did" />
      {!view.auto_stage_ran ? (
        // NOT "0 actions". The stage never ran, which is a different fact and the default one.
        // It speaks for the digest only: the runs listed under it ran on their own triggers.
        <p data-type="body-s" className="text-on-surface-low">
          Auto-execution is off — the digest acted on nothing without you. What it proposes waits for you below.
        </p>
      ) : autoDone.length === 0 ? (
        // Three different facts, and only the last is "it had nothing to do": the stage stopped as
        // a whole (said below), or what it tried did not happen, or nothing was allowed to run.
        view.auto_stopped ? null : notDone > 0 ? (
          <p data-type="body-s" className="text-warn">
            Nothing was done on its own: {notDone === 1 ? 'the one action it was about to take' : `the ${notDone} actions it was about to take`} did not happen. Each is under Needs you, with why.
          </p>
        ) : (
          <p data-type="body-s" className="text-on-surface-low">
            Auto-execution ran and found nothing it was allowed to do on its own.
          </p>
        )
      ) : (
        <ul aria-label="What your machine did" className="flex flex-col gap-s">
          {autoDone.map((row) => (
            <li key={`${row.ordinal}-${row.action_type}`} className="flex items-start gap-m rounded-lg bg-surface-high px-m py-s">
              <div className="min-w-0 flex-1">
                <p data-type="body-s" className="truncate text-on-surface">
                  <span style={fvs(600)}>{doneVerb(row.action_type)}</span>{' '}
                  {row.title || `item ${row.ordinal}`}
                </p>
                <p data-type="caption" className="mt-0.5 text-on-surface-low">
                  because of <code className="font-mono">{row.rule}</code>
                </p>
              </div>
              {row.undoable ? (
                <Button size="xs" variant="secondary" loading={busy === row.reversal} onClick={() => undo(row.reversal)}>
                  <RotateCcw size={12} /> Undo
                </Button>
              ) : (
                // Why there is no button, rather than a button that would fail.
                <span data-type="caption" className="shrink-0 text-on-surface-low">no undo recorded</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {view.auto_stage_ran && autoDone.length > 0 && notDone > 0 && (
        <p data-type="body-s" className="mt-s text-warn">
          {notDone === 1 ? 'One more action' : `${notDone} more actions`} it was about to take did not happen. Each is under Needs you, with why.
        </p>
      )}
      {view.auto_stage_ran && view.auto_stopped && (
        <p data-type="body-s" className="mt-s text-warn">{view.auto_stopped}</p>
      )}

      {/* The runs that ended in the window — what the digest's own body lists under this heading.
          The card counted them in "N items in this window" and showed none of them. */}
      {ran.length > 0 && (
        <ul aria-label="Runs that ended in this window" className="mt-s flex flex-col gap-s">
          {ran.map((row) => (
            <li key={row.ordinal} className="flex items-center gap-m rounded-lg bg-surface-high px-m py-s">
              <p data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{row.title || `item ${row.ordinal}`}</p>
              {row.needs_you && <Badge tone="warn">needs you</Badge>}
              {row.item_permalink && (
                <TextLink href={row.item_permalink} ink="emphasis" size="xs" className="shrink-0"
                  aria-label={`Open the run ${row.title || `item ${row.ordinal}`}`}>
                  open
                </TextLink>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* ── Needs you ── */}
      <SectionHead icon={AlertTriangle} title="Needs you" count={pending.length} />
      {pending.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">Nothing is waiting on you in this digest.</p>
      ) : (
        <ul aria-label="Proposals that need you" className="flex flex-col gap-s">
          {pending.map((row) => (
            <PendingRow key={row.ordinal} row={row} busy={busy} onReply={reply} />
          ))}
        </ul>
      )}
      {help && <p data-type="caption" className="mt-s text-warn" role="status">{help}</p>}
      {carried && view.carry_rule && (
        <p data-type="caption" className="mt-s text-on-surface-low">{view.carry_rule}</p>
      )}

      {/* ── No longer offered: what waited a week without an answer ── */}
      {noLonger.length > 0 && (
        <>
          <SectionHead icon={Clock} title="No longer offered" count={noLonger.length} />
          <ul aria-label="No longer offered" className="flex flex-col gap-s">
            {noLonger.map((row, i) => (
              <li key={`${i}-${row.action_type}-${row.title}`} className="flex items-start gap-m rounded-lg bg-surface-high px-m py-s">
                <div className="min-w-0 flex-1">
                  <p data-type="body-s" className="truncate text-on-surface">
                    <span style={fvs(600)}>{proposedVerb(row.action_type)}</span> {row.title || 'an item'}
                  </p>
                  <p data-type="caption" className="mt-xs text-on-surface-low">{row.note}</p>
                </div>
                {row.item_permalink && (
                  <TextLink href={row.item_permalink} ink="emphasis" size="xs" className="shrink-0"
                    aria-label={`Open ${row.title || 'the item'}`}>
                    open
                  </TextLink>
                )}
              </li>
            ))}
          </ul>
          {!carried && view.carry_rule && (
            <p data-type="caption" className="mt-s text-on-surface-low">{view.carry_rule}</p>
          )}
        </>
      )}

      {/* ── Also waiting: what else the gate kept that no proposal is about ── */}
      {waiting.length > 0 && (
        <>
          <SectionHead icon={Inbox} title="Also waiting" count={waiting.length} />
          <ul aria-label="Also waiting" className="flex flex-col gap-s">
            {waiting.map((row) => (
              <li key={row.ordinal} className="flex items-center gap-m rounded-lg bg-surface-high px-m py-s">
                <p data-type="body-s" className="min-w-0 flex-1 truncate text-on-surface">{row.title || `item ${row.ordinal}`}</p>
                {row.source && <span data-type="caption" className="shrink-0 text-on-surface-low">{row.source}</span>}
                {row.item_permalink && (
                  <TextLink href={row.item_permalink} ink="emphasis" size="xs" className="shrink-0"
                    aria-label={`Open ${row.title || `item ${row.ordinal}`}`}>
                    open
                  </TextLink>
                )}
              </li>
            ))}
          </ul>
        </>
      )}

      {/* ── The ledger ── */}
      <SectionHead icon={ScrollText} title="In the run journal" count={ledger.length} />
      {!view.ledger_complete ? (
        // The provider reported rows it could NOT stamp with a run key. Reporting "none" here
        // would present a recording gap as a result.
        <p data-type="body-s" className="text-warn">
          Some of this run's rationales were not recorded, so this list is incomplete.
        </p>
      ) : ledger.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">This run wrote no ledger rows.</p>
      ) : (
        <ul aria-label="This run's ledger rows" className="flex flex-col gap-1">
          {/* Keyed by place as well: a row's `seq` is not unique in a run's journal (a reply's rows
              and the digest's own were each numbered from 1), and React drops a row whose key
              repeats — which would hide a failure row behind the one it collided with. */}
          {ledger.map((row, i) => (
            <li key={`${i}-${row.kind}-${row.seq}`} data-type="caption" className="flex items-baseline gap-s">
              <code className="shrink-0 font-mono text-on-surface-low">{row.kind}</code>
              <span className="min-w-0 flex-1 truncate text-on-surface-low">
                {row.ordinal ? `#${row.ordinal} ` : ''}{row.action_type ? `${row.action_type} — ` : ''}
                {row.reason || row.outcome || row.verb || row.detail || '—'}
                {row.rule ? <> · <code className="font-mono">{row.rule}</code></> : null}
              </span>
              {row.permalink && (
                <TextLink href={row.permalink} ink="emphasis" className="shrink-0" aria-label={`Open the run journal for ${row.kind}`}>
                  open
                </TextLink>
              )}
            </li>
          ))}
        </ul>
      )}
    </Surface>
  )
}

function DigestSetup({ view, digest }: { view: TriageDigestView; digest: ReturnType<typeof useTriageDigest> }) {
  const { cron, setCron, busy, install } = digest
  const fresh = view.state === 'uninstalled'
  const off = view.state === 'off'
  const Icon = fresh ? Sparkles : off ? Power : Clock
  return <Surface tone="container" radius="xl" className="mb-l border border-outline/30 p-l shadow-sm">
    <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-m">
      <Icon size={18} className={`mt-1 ${fresh ? 'text-primary' : 'text-on-surface-low'}`} />
      <div>
        <Header title="Morning triage" trailing={!fresh ? <Badge tone="muted">{off ? 'Off' : 'Scheduled'}</Badge> : undefined} />
        {fresh ? <>
          <p data-type="body-s" className="mt-m text-on-surface-low">
            One scheduled digest: collect what accumulated across your inbox, channels and background runs,
            filter it through your own rules, and propose what to do. It proposes — it executes nothing
            unless you switch auto-execution on.
          </p>
          <div className="mt-m flex flex-wrap items-end gap-m">
            <label className="grid gap-1"><span data-type="caption" className="text-on-surface-low">When (cron)</span>
              <div className="w-40"><TextInput value={cron} onChange={setCron} placeholder="0 8 * * *" size="sm" mono ariaLabel="Digest schedule (cron)" /></div>
            </label>
            <Button size="sm" variant="primary" loading={busy === 'install'} onClick={() => install(cron.trim() || undefined)}>Install</Button>
          </div>
          <p data-type="caption" className="mt-s text-on-surface-low">
            Installing adds a schedule you can edit or pause any time. Turn the digest itself on under{' '}
            <a href="#/settings/inbox" className="text-primary-emphasis underline">Settings → Inbox</a>.
          </p>
        </> : off ? <>
          <p data-type="body-s" className="mt-m text-on-surface-low">
            The digest is switched off, so nothing is collected and nothing is spent. Your schedule
            {view.schedule?.cron ? <> (<code className="font-mono">{view.schedule.cron}</code>)</> : null} and your
            triage rules are kept — turning it back on picks up exactly where you left off.
          </p>
          <p data-type="body-s" className="mt-s"><TextLink href="#/settings/inbox" ink="emphasis">Turn triage on in Settings → Inbox</TextLink></p>
        </> : <>
          <p data-type="body-s" className="mt-m text-on-surface-low">
            Installed and on. No digest has run yet
            {view.schedule?.cron ? <> — the schedule is <code className="font-mono">{view.schedule.cron}</code></> : null}.
            This is not an empty digest: there hasn't been one.
          </p>
          {view.schedule_drift && <p data-type="caption" className="mt-s text-warn">
            The schedule's own switch disagrees with your triage setting.{' '}
            <Button size="xs" variant="ghost-accent" onClick={() => install()}>Reconcile it</Button>
          </p>}
        </>}
      </div>
    </div>
  </Surface>
}

function PendingRow({ row, busy, onReply }: { row: TriagePending; busy: string; onReply: (text: string) => void }) {
  const n = row.ordinal
  // 🪤 THE ROW IS THE UNIT A SCREEN READER NAVIGATES, AND IT HAD NO NAME. Its two sibling lists
  // (`Proposals that need you`, `This run's ledger rows`) name themselves, but a list item is
  // announced by its own content — and this row's content is a `#{n}` span, a verb span and a title
  // in one `<p>`, then a badge, a source and two links. Landing on it announced the whole subtree in
  // reading order, so the verb and title arrived after the ordinal and before three controls, with
  // nothing distinguishing "which proposal is this" from "what can I do to it".
  //
  // The name is assembled from the SAME fields the visible row shows, in the same order, through the
  // same `proposedVerb` and the same `item ${n}` fallback — so the announced row and the seen row
  // cannot disagree. In particular the verb is NOT conditional on `action_type`: `proposedVerb('')`
  // answers 'Act on', which is what the paragraph below prints, and a guard here would have named the row
  // differently from the row itself in exactly the case where the field is missing.
  // The task a Yes files, by the title its run recorded: what she approves is what she reads.
  const task = row.action_config?.title || ''
  const label = `Proposal ${n}: ${proposedVerb(row.action_type)} ${row.title || `item ${n}`}${task ? `, as the task “${task}”` : ''}${row.source ? `, ${row.source}` : ''}${row.carried_over ? ', carried over from an earlier digest' : ''}`
  // An answered proposal keeps its row and says what was answered. Removing it would make a reply
  // look like it did nothing; re-offering the buttons would invite a second, duplicate answer.
  return (
    <li aria-label={label} className="flex flex-col gap-s rounded-lg bg-surface-high px-m py-s sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p data-type="body-s" className="truncate text-on-surface">
          <span className="mr-1 text-on-surface-low">#{n}</span>
          <span style={fvs(600)}>{proposedVerb(row.action_type)}</span> {row.title || `item ${n}`}
        </p>
        {task && (
          <p data-type="caption" className="truncate text-on-surface">as the task “{task}”</p>
        )}
        <p data-type="caption" className="mt-0.5 flex flex-wrap items-center gap-s">
          <TierBadge tier={row.tier} clamped={row.clamped} />
          {row.source && <span className="text-on-surface-low">{row.source}</span>}
          {/* Same reason as the install-hint link above: this sits in a `<p>` beside the tier badge
              and the source label, so it IS inside a text block and needs the rest-state underline. */}
          {row.item_permalink && (
            <a href={row.item_permalink} className="text-primary-emphasis underline">the item</a>
          )}
        </p>
        {/* An earlier digest proposed it and you have not answered it: the server's sentence,
            with how long it has waited. */}
        {row.carried_over && row.carried_note && (
          <p data-type="caption" className="mt-xs text-on-surface-low">{row.carried_note}</p>
        )}
        {/* The digest tried this on its own and it did not happen, or the answer's yes did not:
            the server's sentence, with why and what to do next. A row without it is a proposal
            nobody has tried. */}
        {(row.answered ? row.answer_not_done : row.not_done) && (
          <p data-type="caption" className="mt-xs text-warn">{row.answered ? row.answer_not_done : row.not_done}</p>
        )}
      </div>
      {row.answered ? (
        <span data-type="caption" className="shrink-0 text-on-surface-low">
          You answered <span style={fvs(600)}>{row.answer || 'this'}</span>
        </span>
      ) : (
        <div className="flex shrink-0 flex-wrap gap-1">
          <Button size="xs" variant="primary" loading={busy === `${n} yes`} onClick={() => onReply(`${n} yes`)}>Yes</Button>
          <Button size="xs" variant="secondary" loading={busy === `${n} no`} onClick={() => onReply(`${n} no`)}>No</Button>
          {/* "Always" is offered ONLY when the run recorded a pattern to teach. Without one there
              is nothing narrow to remember, and inventing a pattern from the action type would
              teach a rule far broader than the one thing the user is looking at. */}
          {row.pattern_key ? (
            <>
              <Button size="xs" variant="ghost" loading={busy === `always yes ${n}`} onClick={() => onReply(`always yes ${n}`)}
                title={`Always allow ${row.pattern_key}`}>Always</Button>
              <Button size="xs" variant="ghost" loading={busy === `always no ${n}`} onClick={() => onReply(`always no ${n}`)}
                title={`Never allow ${row.pattern_key}`}>Never</Button>
            </>
          ) : (
            <span data-type="caption" className="self-center text-on-surface-low">no pattern to remember</span>
          )}
        </div>
      )}
    </li>
  )
}

const TIER_LABEL: Record<string, string> = {
  trivial: 'trivial',
  low: 'low risk',
  medium: 'needs a look',
  high: 'high risk',
}

const BADGE_SKIN = {
  danger: 'bg-danger/15 text-on-danger-tint', warn: 'bg-warn/15 text-warn',
  primary: 'bg-primary/15 text-on-primary-tint', muted: 'bg-surface-highest text-on-surface-low',
} as const
function TierBadge({ tier, clamped }: { tier: string; clamped: boolean }) {
  const tones: Record<string, keyof typeof BADGE_SKIN> = { high: 'danger', medium: 'warn', trivial: 'muted', '': 'warn' }
  return <Badge tone={tones[tier] ?? 'primary'}>{tier ? TIER_LABEL[tier] || tier : 'untiered'}{clamped && ' (raised)'}</Badge>
}
function Badge({ tone, children }: { tone: keyof typeof BADGE_SKIN; children: React.ReactNode }) {
  return <span data-type="caption" className={`inline-flex rounded-md px-2 py-0.5 ${BADGE_SKIN[tone]}`} style={fvs(500)}>{children}</span>
}
function Header({ title, trailing }: { title: string; trailing?: React.ReactNode }) {
  return <header className="flex items-start justify-between gap-m border-b border-outline/20 pb-m">
    <h2 data-type="title-m" className="min-w-0 truncate text-on-surface" style={fvs(600)}>{title}</h2>{trailing}
  </header>
}
function SectionHead({ icon: Icon, title, count }: { icon: typeof CheckCheck; title: string; count?: number }) {
  return <h3 data-type="label-s" className="mb-m mt-l flex items-center gap-s text-on-surface" style={fvs(600)}>
    <Icon size={14} className="shrink-0 text-on-surface-low" aria-hidden="true" />
    <span>{title}</span>{count !== undefined && count > 0 && <span className="rounded-md bg-surface-high px-1.5 text-on-surface-low" style={fvs(400)}>{count}</span>}
  </h3>
}


function NoticeLine({ notice }: { notice?: TriageDigestNotice }) {
  if (!notice) return null
  if (!notice.known) return <p data-type="caption" className="mt-s text-warn">Your notification settings could not be read. Delivery policy is unknown.</p>
  const explain = (mode: TriageNoticeOutcome) => ({
    immediate: 'announces it immediately', badge: 'adds it to notifications without an interruption',
    digest: 'queues it for your notification summary', never: 'does not announce it',
    suppressed: 'does not announce it', dropped: 'does not announce it',
  })[mode]
  return <p data-type="caption" className="mt-s text-on-surface-low">
    Your current notification policy {explain(notice.outside)}.
    {notice.quiet_hours.enabled && <> Inside quiet hours {notice.quiet_hours.start}–{notice.quiet_hours.end}, it {explain(notice.inside)}.</>}
    {' '}The digest remains here and in the run journal.
  </p>
}

const PROPOSED_VERB: Record<string, string> = {
  archive: 'Archive',
  mute_thread: 'Mute',
  dismiss: 'Dismiss',
  reply_draft: 'Draft a reply to',
  create_task: 'File a task for',
}

/** What an action that LANDED did. Only `auto_done` rows, which the server fills with landed
 *  actions alone. */
const DONE_VERB: Record<string, string> = {
  archive: 'Archived',
  mark_read: 'Marked read',
  mute_thread: 'Muted',
  dismiss: 'Dismissed',
  reply_draft: 'Drafted a reply to',
  create_task: 'Filed a task for',
}

/** The action word, or the raw type when we have no phrasing for it — never a guess that reads
 *  gentler than the thing it names. */
function proposedVerb(actionType: string): string {
  return PROPOSED_VERB[actionType] || actionType || 'Act on'
}

function doneVerb(actionType: string): string {
  return DONE_VERB[actionType] || actionType || 'Acted on'
}
