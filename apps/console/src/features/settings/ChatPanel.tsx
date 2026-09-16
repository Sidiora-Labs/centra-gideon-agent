import { useEffect, useState } from 'react'
import { api, type DashboardConfig, type SessionTemplate } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useAgentCatalog, ensureBindableAgentName } from '../../shared/data/agents'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, Row, Toggle, SegPills, SavedToast } from './settingsUI'
import { Combobox } from '../../shared/ui/Combobox'
import { NumberField } from '../../shared/ui/forms'
import { IconButton } from '../../shared/ui/IconButton'
import { confirmDelete } from '../../shared/ui/dialog'
import { Trash2 } from 'lucide-react'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'

const RESTORE_WINDOWS = [
  { key: '15', label: '15 min' }, { key: '30', label: '30 min' },
  { key: '60', label: '1 hour' }, { key: '240', label: '4 hours' }, { key: '0', label: 'All' },
]

export function ChatPanel() {
  const [cfg, setCfg] = useState<DashboardConfig | null>(null)
  const [session, setSession] = useState<Record<string, unknown> | null>(null)
  const [routing, setRouting] = useState<Record<string, unknown> | null>(null)
  const [resilience, setResilience] = useState<Record<string, unknown> | null>(null)
  const [checkpoints, setCheckpoints] = useState<Record<string, unknown> | null>(null)
  const { options: agentOptions, discovered } = useAgentCatalog()

  const { data, error: loadErr, refresh } = useQuery('settings:chat', async () => {
    const [dash, plaw] = await Promise.all([
      api.dashboardConfig().catch(() => null),
      api.gideonConfig(),
    ])
    return {
      cfg: dash,
      session: (plaw.session ?? {}) as Record<string, unknown>,
      routing: (plaw.agents_routing ?? {}) as Record<string, unknown>,
      resilience: (plaw.resilience ?? {}) as Record<string, unknown>,
      checkpoints: (plaw.checkpoints ?? {}) as Record<string, unknown>,
    }
  }, { persist: true })

  useEffect(() => {
    if (data) {
      setCfg(data.cfg); setSession(data.session); setRouting(data.routing)
      setResilience(data.resilience); setCheckpoints(data.checkpoints)
    }
  }, [data])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg || !session || !routing || !resilience || !checkpoints) return <FormSkeleton sections={3} what="settings" />

  return (
    <div>
      <PanelHeader title="Chat" hint="How sessions restore, how messages display, and how long context lives. These follow you across browsers." />

      <SessionsSection cfg={cfg} setCfg={setCfg} />
      <MessagesSection cfg={cfg} setCfg={setCfg} />
      <MidTurnSection resilience={resilience} setResilience={setResilience} />
      <RoutingSection routing={routing} setRouting={setRouting} />
      <LifecycleSection session={session} setSession={setSession} agentOptions={agentOptions} discovered={discovered} />
      <CheckpointsSection checkpoints={checkpoints} setCheckpoints={setCheckpoints} />
      <StartersSection />
    </div>
  )
}

function StartersSection() {
  const [items, setItems] = useState<SessionTemplate[] | null>(null)

  useEffect(() => {
    let live = true
    api.sessionTemplates()
      .then((t) => { if (live) setItems(t) })
      .catch(() => { if (live) setItems([]) })
    return () => { live = false }
  }, [])

  async function remove(t: SessionTemplate) {
    if (!(await confirmDelete('starter', t.name))) return
    try {
      await api.deleteSessionTemplate(t.id)
    } catch (e) {
      notify(`Couldn't delete this starter: ${String((e as Error)?.message || e)}`, 'error')
      return
    }
    setItems((prev) => (prev ?? []).filter((x) => x.id !== t.id))
    invalidateKeys('chat:starters')
  }

  return (
    <Section title="Chat starters" hint="Reusable setups — agent, model and reasoning effort. Save one from a chat's header; they appear on the new-chat screen.">
      <RowGroup>
        {items === null ? (
          <p data-type="body-s" className="py-3 text-on-surface-low">Loading…</p>
        ) : items.length === 0 ? (
          <p data-type="body-s" className="py-3 text-on-surface-low">
            No starters yet. Open a chat, set it up how you like, then use “Save as starter” in its header.
          </p>
        ) : items.map((t) => (
          <Row key={t.id} label={t.name} hint={[t.agent, t.model, t.reasoning_effort].filter(Boolean).join(' · ') || 'Uses your defaults'}>
            <IconButton icon={Trash2} label={`Delete ${t.name}`} iconSize={16} size={32} tone="danger" onClick={() => remove(t)} />
          </Row>
        ))}
      </RowGroup>
    </Section>
  )
}

const MID_TURN_POLICIES = [
  { key: 'queue', label: 'Queue' },
  { key: 'steer', label: 'Steer' },
  { key: 'cancel_and_replace', label: 'Replace' },
] as const

function MidTurnSection({ resilience, setResilience }: {
  resilience: Record<string, unknown>; setResilience: (r: Record<string, unknown>) => void
}) {
  const [saved, flash] = useSavedFlash()
  const policy = String(resilience.mid_turn_policy ?? 'queue')
  const patch = (value: string) => {
    const prev = resilience.mid_turn_policy
    setResilience({ ...resilience, mid_turn_policy: value })
    api.patchConfig('resilience.mid_turn_policy', value).then(flash).catch((e) => {
      setResilience({ ...resilience, mid_turn_policy: prev })
      notify(`Couldn't save mid-turn policy: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Mid-turn messages" hint="What happens when you send something while an answer is still being written.">
      <RowGroup>
        <Row label="Default handling"
          hint="Queue: deliver it as the next turn. Steer: fold it into the answer being written, where the running agent supports that — otherwise it queues. Replace: stop the current answer and start over with the new message. Unattended work (loops, cron, subagents) always queues.">
          <div className="flex items-center gap-2">
            <SavedToast show={saved} />
            <SegPills ariaLabel="Default handling" value={policy} onChange={patch} options={[...MID_TURN_POLICIES]} />
          </div>
        </Row>
        {policy === 'steer' && (
          <p data-type="caption" className="pb-3 text-on-surface-low">
            Steering reaches the running answer on the built-in agent. Connected CLI
            agents (ACP) don't expose a mid-turn seam yet, so a message there queues
            instead — either way it appears above the composer, never dropped.
          </p>
        )}
      </RowGroup>
    </Section>
  )
}

function RoutingSection({ routing, setRouting }: { routing: Record<string, unknown>; setRouting: (r: Record<string, unknown>) => void }) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = routing[key]
    setRouting({ ...routing, [key]: value })
    api.patchConfig(`agents_routing.${key}`, value).then(flash).catch(() => {
      setRouting({ ...routing, [key]: prev })
      notify(`Couldn't save ${label ?? key}`, 'error')
    })
  }
  const enabled = routing.enabled !== false
  return (
    <Section title="Agent routing" hint="Suggest a better-fit specialist agent when a message matches one — you always confirm before it re-targets the chat.">
      <RowGroup>
        <Row label="Suggest specialists" hint="When a message in a default-agent chat fits an installed specialist, show a one-click 'route to <agent>?' chip. Never routes silently.">
          <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={enabled} onChange={(v) => patch('enabled', v)} label="Suggest specialists" /></div>
        </Row>
        {enabled && (
          <NumberRow label="Confidence threshold" hint="Minimum match confidence before a routing chip appears. Higher = fewer, surer suggestions." value={Number(routing.min_confidence ?? 0.62)} min={0.3} max={0.95} step={0.01} onCommit={(n, l) => patch('min_confidence', n, undefined, l)} saved={saved} />
        )}
        {enabled && (
          <NumberRow label="Dismiss cooldown" hint="After you dismiss a suggestion for an agent, suppress it for this long (three dismissals mute it until you re-enable)." value={Number(routing.cooldown_hours ?? 24)} min={0} max={720} step={1} suffix="h" onCommit={(n, l) => patch('cooldown_hours', n, undefined, l)} saved={saved} />
        )}
      </RowGroup>
    </Section>
  )
}

function SessionsSection({ cfg, setCfg }: { cfg: DashboardConfig; setCfg: (c: DashboardConfig) => void }) {
  const [saved, flash] = useSavedFlash()
  const save = (patch: Partial<DashboardConfig>) => {
    setCfg({ ...cfg, ...patch })
    api.saveDashboardConfig(patch).then(() => { invalidateKeys('chat:stream-reveal'); flash() }).catch((e) => {
      notify(`Couldn't save this chat setting: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Sessions" hint="What happens to your chats on restart, and while the agent is busy.">
      <RowGroup>
        { }
        <Row label="Restore sessions on startup" hint="Re-open recently active sessions when the app starts.">
          <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={cfg.restore_sessions} onChange={(v) => save({ restore_sessions: v })} label="Restore sessions on startup" /></div>
        </Row>
        {cfg.restore_sessions && (
          <Row label="Restore window" hint="How recently active a session must be to re-open.">
            <SegPills ariaLabel="Restore window" value={String(cfg.restore_window_minutes)} onChange={(v) => save({ restore_window_minutes: Number(v) })} options={RESTORE_WINDOWS} />
          </Row>
        )}
        <Row label="Merge queued messages" hint="While the agent is busy, combine follow-ups into one labeled prompt instead of queueing separately.">
          <Toggle on={cfg.merge_queued_messages} onChange={(v) => save({ merge_queued_messages: v })} label="Merge queued messages" />
        </Row>
        <Row label="Auto-tag new chats" hint="When a chat's title is generated, also propose and assign tags in the same pass. Never touches chats you've tagged yourself, or incognito/temporary chats.">
          <Toggle on={cfg.auto_tag_sessions} onChange={(v) => save({ auto_tag_sessions: v })} label="Auto-tag new chats" />
        </Row>
      </RowGroup>
    </Section>
  )
}

function MessagesSection({ cfg, setCfg }: { cfg: DashboardConfig; setCfg: (c: DashboardConfig) => void }) {
  const [saved, flash] = useSavedFlash()
  const save = (patch: Partial<DashboardConfig>) => {
    setCfg({ ...cfg, ...patch })
    api.saveDashboardConfig(patch).then(() => {
      invalidateKeys('chat:stream-reveal')
      invalidateKeys('chat:show-timestamps')
      invalidateKeys('chat:send-on-enter')
      flash()
    }).catch((e) => {
      notify(`Couldn't save this chat setting: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  return (
    <Section title="Messages" hint="How messages and tool activity render in the chat.">
      <RowGroup>
        {
}
        <Row label="Send on Enter" hint={cfg.send_on_enter ? 'Enter sends · Shift+Enter for a newline.' : 'Enter inserts a newline · sending is button-only.'}>
          <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={cfg.send_on_enter} onChange={(v) => save({ send_on_enter: v })} label="Send on Enter" /></div>
        </Row>
        <Row label="Show timestamps" hint="Display a time on each message.">
          <Toggle on={cfg.show_timestamps} onChange={(v) => save({ show_timestamps: v })} label="Show timestamps" />
        </Row>
        <Row label="Show thinking inline" hint="Show intermediate reasoning between tool calls instead of collapsing it.">
          <Toggle on={cfg.show_thinking_inline} onChange={(v) => save({ show_thinking_inline: v })} label="Show thinking inline" />
        </Row>
        <Row label="Simplified tool names" hint="Tool pills show a simplified purpose instead of the exact command.">
          <Toggle on={cfg.simplified_tool_names} onChange={(v) => save({ simplified_tool_names: v })} label="Simplified tool names" />
        </Row>
        <Row label="Follow-up suggestions" hint="After each reply, show 2-3 suggested next messages (one small background call; never blocks the turn). Skipped for temporary/incognito chats; silent with no model bound.">
          <Toggle on={cfg.followup_chips} onChange={(v) => save({ followup_chips: v })} label="Follow-up suggestions" />
        </Row>
        {
}
        <Row label="Offer “Check this work”" hint="After a turn that did real multi-step work, offer a chip that re-derives and re-runs the checks against what the turn claimed. Only ever an offer — the verification cost is spent on your click, never automatically.">
          <Toggle on={cfg.offer_check_work} onChange={(v) => save({ offer_check_work: v })} label="Offer 'Check this work'" />
        </Row>
        {
}
        <Row label="Share screen in chat" hint="Adds a “Share screen” control to the composer. With it on, a message can carry ONE frame of a screen or window you pick in your browser's own share dialog — held in memory for that single turn, never written to disk. Your browser shows its own capture indicator the whole time. Off means the control is hidden and the server refuses any frame.">
          <Toggle on={cfg.screen_share_enabled} onChange={(v) => save({ screen_share_enabled: v })} label="Share screen in chat" />
        </Row>
        <Row label="Streaming text reveal" hint="Smooth: steady word-by-word reveal decoupled from network chunks (never lags). Immediate: render each chunk the instant it arrives.">
          <SegPills ariaLabel="Streaming text reveal" value={cfg.stream_reveal} onChange={(v) => save({ stream_reveal: v as 'smooth' | 'immediate' })}
            options={[{ key: 'smooth', label: 'Smooth' }, { key: 'immediate', label: 'Immediate' }]} />
        </Row>
        <Row label="Widget density" hint="How aggressively the agent uses inline widgets for visual content.">
          <SegPills ariaLabel="Widget density" value={cfg.widget_density} onChange={(v) => save({ widget_density: v as 'more' | 'less' })}
            options={[{ key: 'more', label: 'More' }, { key: 'less', label: 'Less' }]} />
        </Row>
      </RowGroup>
    </Section>
  )
}

function CheckpointsSection({ checkpoints, setCheckpoints }: {
  checkpoints: Record<string, unknown>; setCheckpoints: (c: Record<string, unknown>) => void
}) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = checkpoints[key]
    setCheckpoints({ ...checkpoints, [key]: value })
    api.patchConfig(`checkpoints.${key}`, value).then(flash).catch((e) => {
      setCheckpoints({ ...checkpoints, [key]: prev })
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  const on = checkpoints.enabled !== false
  return (
    <Section title="File checkpoints" hint="Before the agent's first write to a file in a turn, its current bytes are saved so /rewind-to-turn can restore them. Files only — never the conversation. Credential files (.env, keys) are never copied, so they are never restored either.">
      <RowGroup>
        <Row label="Back up files before an edit" hint={on ? 'A wrong edit is recoverable with /rewind-to-turn N.' : 'Off — a wrong edit is gone. Nothing is being recorded.'}>
          <div className="flex items-center gap-2"><SavedToast show={saved} /><Toggle on={on} onChange={(v) => patch('enabled', v)} label="Back up files before an edit" /></div>
        </Row>
        {on && (
          <>
            <NumberRow label="Store cap per chat" hint="Megabytes of saved file contents kept per chat. When a new backup would exceed this, the oldest turns are dropped — rewinding past them stops being possible. 0 = no byte cap." value={Number(checkpoints.max_mb ?? 200)} min={0} max={100000} step={10} suffix="MB" onCommit={(n, l) => patch('max_mb', n, undefined, l)} saved={saved} />
            <NumberRow label="Turns kept" hint="How many recent turns you can rewind to. Older turns are dropped." value={Number(checkpoints.max_turns ?? 50)} min={1} max={1000} step={1} onCommit={(n, l) => patch('max_turns', n, undefined, l)} saved={saved} />
            <NumberRow label="Largest file backed up" hint="A file bigger than this is noted but not copied, so a rewind reports it as not captured instead of restoring it. Keeps one big write from filling the whole store. 0 = no limit." value={Number(checkpoints.max_file_mb ?? 8)} min={0} max={10000} step={1} suffix="MB" onCommit={(n, l) => patch('max_file_mb', n, undefined, l)} saved={saved} />
          </>
        )}
      </RowGroup>
    </Section>
  )
}

function LifecycleSection({ session, setSession, agentOptions, discovered }: {
  session: Record<string, unknown>; setSession: (s: Record<string, unknown>) => void
  agentOptions: import('../../shared/data/agents').AgentOption[]; discovered: Record<string, import('../../shared/data/api').DiscoveredAgent[]>
}) {
  const [saved, flash] = useSavedFlash()
  const patch = (key: string, value: unknown, _cb?: () => void, label?: string) => {
    const prev = session[key]
    setSession({ ...session, [key]: value })
    api.patchConfig(`session.${key}`, value).then(flash).catch((e) => {
      setSession({ ...session, [key]: prev })
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }
  const poolSize = Number(session.pool_size ?? 0)
  return (
    <Section title="Context & lifecycle" hint="Keep long sessions productive and control how warm sessions are kept ready.">
      <RowGroup>
        <NumberRow label="Auto-compact threshold" hint="Context-usage % that triggers compaction. Lower = more frequent." value={Number(session.autocompact_pct ?? 90)} min={5} max={90} step={1} suffix="%" onCommit={(n, l) => patch('autocompact_pct', n, undefined, l)} saved={saved} />
        <NumberRow label="Idle timeout" hint="Auto-close an idle session after this long. 0 = never." value={Number(session.timeout_secs ?? 0)} min={0} max={86400} step={60} suffix="s" onCommit={(n, l) => patch('timeout_secs', n, undefined, l)} saved={saved} />
        <AutoArchiveRow days={Number(session.auto_archive_days ?? 30)} onCommit={(n, l) => patch('auto_archive_days', n, undefined, l)} saved={saved} />

        <Row label="Warm pool size" hint="Pre-started sessions kept ready for an instant first turn. 0 = off.">
          <NumberField value={poolSize} min={0} max={10} step={1} onChange={(n) => patch('pool_size', n)} ariaLabel="Warm pool size" />
        </Row>
        {poolSize > 0 && (
          <>
            <Row label="Warm pool agent" hint="Which agent the warm sessions pre-start as (native or a connected ACP-runtime agent). Empty uses the default agent.">
              <div className="w-56">
                <Combobox
                  value={String(session.pool_agent ?? '')}
                  options={[{ value: '', label: '— default —' }, ...agentOptions,
                    ...(session.pool_agent && !agentOptions.some((o) => o.value === session.pool_agent) ? [{ value: String(session.pool_agent), label: String(session.pool_agent), group: 'Current' }] : [])]}
                  placeholder="— default —" emptyText="No agents"
                  onChange={async (v) => { const name = v ? await ensureBindableAgentName(v, discovered) : ''; patch('pool_agent', name) }} />
              </div>
            </Row>
            <NumberRow label="Warm pool TTL" hint="Recycle a warm session after this long unused." value={Number(session.pool_ttl_secs ?? 1800)} min={0} max={7200} step={60} suffix="s" onCommit={(n, l) => patch('pool_ttl_secs', n, undefined, l)} saved={saved} />
          </>
        )}
      </RowGroup>
    </Section>
  )
}

function AutoArchiveRow({ days, onCommit, saved }: {
  days: number;

  onCommit: (n: number, label?: string) => void; saved: boolean
}) {
  const [pending, setPending] = useState<number | null>(null)
  const [preview, setPreview] = useState<{ count: number; enabled: boolean } | null>(null)
  const shown = pending ?? days

  useEffect(() => {
    if (shown <= 0) { setPreview(null); return }
    let live = true
    api.autoArchiveSessions({ dry_run: true })
      .then((r) => { if (live) setPreview({ count: r.count, enabled: r.enabled }) })
      .catch(() => { if (live) setPreview(null) })
    return () => { live = false }
  }, [shown])

  return (
    <Row
      label="Auto-archive after"
      hint="Archive chats with no activity for this long. Archived chats stay searchable and restore in one click — nothing is deleted. 0 = off."
    >
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        {shown > 0 && preview?.enabled && (
          <span data-type="caption" className="text-on-surface-variant tabular-nums">
            {preview.count === 0 ? 'none stale now' : `${preview.count} stale now`}
          </span>
        )}
        <NumberField
          value={shown} min={0} max={3650} step={1} ariaLabel="Auto-archive after (days)"
          onChange={(n) => { setPending(n); onCommit(n, 'Auto-archive after (days)') }}
        />
        <span data-type="caption" className="text-on-surface-variant">{shown > 0 ? 'days' : 'off'}</span>
      </div>
    </Row>
  )
}

function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [saved, () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }]
}

function NumberRow({ label, hint, value, min, max, step, suffix, onCommit, saved }: {
  label: string; hint?: string; value: number; min: number; max: number; step?: number; suffix?: string
  onCommit: (n: number, label?: string) => void; saved: boolean
}) {
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <NumberField value={value} min={min} max={max} step={step} onChange={(n) => onCommit(n, label)} ariaLabel={label} />
        {suffix && <span data-type="caption" className="w-6 text-on-surface-low">{suffix}</span>}
      </div>
    </Row>
  )
}
