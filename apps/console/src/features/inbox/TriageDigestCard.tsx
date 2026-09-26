import { AlertTriangle, CheckCheck, Clock, ExternalLink, Power, RotateCcw, ScrollText, Sparkles } from 'lucide-react'
import { type TriagePending, type TriageDigestView } from '../../shared/data/api'
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
  const ledger = view.machine_did || []

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
      <CommitmentDecisionsCard decisions={view.commitment_decisions || []} onRefresh={refresh} />

      {view.quiet_hours?.known === false ? (
        <p data-type="caption" className="mt-s text-warn">
          Your notification settings could not be read, so whether this digest reached your notifications is unknown.
        </p>
      ) : view.quiet_hours?.mute_all ? (
        <p data-type="caption" className="mt-s text-on-surface-low">
          All notifications are muted, so this digest is here and in the run journal but was not announced.
        </p>
      ) : view.quiet_hours?.enabled ? (
        <p data-type="caption" className="mt-s text-on-surface-low">
          Quiet hours {view.quiet_hours.start}–{view.quiet_hours.end}: a digest that lands inside that window is
          held back from your notifications. It is still here, and in the run journal.
        </p>
      ) : null}

      {view.budget_breached && (
        <div className="mt-m">
          <InlineError icon>
            The daily budget ran out mid-digest, so the rest stayed pending: {view.budget_reason || 'no reason recorded'}
          </InlineError>
        </div>
      )}

      <SectionHead icon={CheckCheck} title="What your machine did" />
      {!view.auto_stage_ran ? (

        <p data-type="body-s" className="text-on-surface-low">
          Auto-execution is off — nothing ran without you. Everything below is a proposal.
        </p>
      ) : autoDone.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">
          Auto-execution ran and found nothing it was allowed to do on its own.
        </p>
      ) : (
        <ul aria-label="What your machine did" className="flex flex-col gap-s">
          {autoDone.map((row) => (
            <li key={`${row.ordinal}-${row.action_type}`} className="flex items-start gap-m rounded-xl border border-outline/20 bg-surface-high px-m py-m">
              <div className="min-w-0 flex-1">
                <p data-type="body-s" className="truncate text-on-surface">
                  <span style={fvs(600)}>{verbFor(row.action_type)}</span>{' '}
                  {row.title || `item ${row.ordinal}`}
                </p>
                <p data-type="caption" className="mt-0.5 text-on-surface-low">
                  {row.ok ? 'because of' : 'failed —'} <code className="font-mono">{row.rule}</code>
                  {row.error ? <> · {row.error}</> : null}
                </p>
              </div>
              {row.undoable ? (
                <Button size="xs" variant="secondary" loading={busy === row.reversal} onClick={() => undo(row.reversal)}>
                  <RotateCcw size={12} /> Undo
                </Button>
              ) : (

                <span data-type="caption" className="shrink-0 text-on-surface-low">no undo recorded</span>
              )}
            </li>
          ))}
        </ul>
      )}

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

      <SectionHead icon={ScrollText} title="In the run journal" count={ledger.length} />
      {!view.ledger_complete ? (

        <p data-type="body-s" className="text-warn">
          Some of this run's rationales were not recorded, so this list is incomplete.
        </p>
      ) : ledger.length === 0 ? (
        <p data-type="body-s" className="text-on-surface-low">This run wrote no ledger rows.</p>
      ) : (
        <ul aria-label="This run's ledger rows" className="flex flex-col gap-1">
          {ledger.map((row) => (
            <li key={`${row.kind}-${row.seq}`} data-type="caption" className="flex items-baseline gap-s">
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
  const subject = row.title ? `#${n} ${row.title}` : `item #${n}`

  return (
    <li className="flex flex-col gap-s rounded-xl border border-outline/20 bg-surface-high px-m py-m sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <p data-type="body-s" className="truncate text-on-surface">
          <span className="mr-1 text-on-surface-low">#{n}</span>
          <span style={fvs(600)}>{verbFor(row.action_type)}</span> {row.title || `item ${n}`}
        </p>
        <p data-type="caption" className="mt-0.5 flex flex-wrap items-center gap-s">
          <TierBadge tier={row.tier} clamped={row.clamped} />
          {row.source && <span className="text-on-surface-low">{row.source}</span>}

          {row.item_permalink && (
            <a href={row.item_permalink} aria-label={`Open item: ${subject}`} className="text-primary-emphasis underline">the item</a>
          )}
        </p>
      </div>
      {row.answered ? (
        <span data-type="caption" className="shrink-0 text-on-surface-low">
          You answered <span style={fvs(600)}>{row.answer || 'this'}</span>
        </span>
      ) : (
        <div className="flex shrink-0 flex-wrap gap-1">
          {[
            { text: 'Yes', command: `${n} yes`, variant: 'primary' as const, title: undefined },
            { text: 'No', command: `${n} no`, variant: 'secondary' as const, title: undefined },
            ...(row.pattern_key ? [
              { text: 'Always', command: `always yes ${n}`, variant: 'ghost' as const, title: `Always allow ${row.pattern_key}` },
              { text: 'Never', command: `always no ${n}`, variant: 'ghost' as const, title: `Never allow ${row.pattern_key}` },
            ] : []),
          ].map(action => <Button key={action.command} size="xs" variant={action.variant} title={action.title} ariaLabel={`${action.text}: ${subject}`}
            loading={busy === action.command} onClick={() => onReply(action.command)}>{action.text}</Button>)}
          {!row.pattern_key && <span data-type="caption" className="self-center text-on-surface-low">no pattern to remember</span>}
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

const ACTION_VERB: Record<string, string> = {
  archive: 'Archived',
  mark_read: 'Marked read',
  mute_thread: 'Muted',
  dismiss: 'Dismissed',
  reply_draft: 'Drafted a reply to',
  create_task: 'Filed a task for',
}

function verbFor(actionType: string): string {
  const label = Object.prototype.hasOwnProperty.call(ACTION_VERB, actionType) ? ACTION_VERB[actionType] : undefined
  return label || actionType || 'Acted on'
}
