import { useCallback, useEffect, useState } from 'react'
import { api, type RunnerRow } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useAgentCatalog, ensureBindableAgentName, type AgentOption } from '../../shared/data/agents'
import { useQuery } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, Row, SegPills, SavedToast, StrListField, ToggleRow } from './settingsUI'
import { Combobox } from '../../shared/ui/Combobox'
import { FieldError, NumberField, TextInput } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { accentChip } from '../../shared/theme/accent'
import { persistClaim } from '../../lib/persistClaim'

type AgentCfg = Record<string, unknown>

export function AgentDefaultsPanel() {
  const [cfg, setCfg] = useState<AgentCfg | null>(null)
  const { options: agentOptions, loading: agentsLoading, discovered } = useAgentCatalog()
  const [defaultAgent, setDefaultAgent] = useState('')
  const [tmuxAvailable, setTmuxAvailable] = useState<boolean | undefined>()

  useEffect(() => {
    Promise.resolve().then(() => api.terminalSessions())
      .then((r) => setTmuxAvailable(r.persist_available))
      .catch(() => {})
  }, [])

  const { data, error: loadErr, refresh } = useQuery('settings:agent-defaults', async () => {
    const [plaw, agents] = await Promise.all([
      api.gideonConfig().then((c) => (c.agent ?? {}) as AgentCfg),
      api.agents().then((d) => d.default_agent).catch(() => ''),
    ])
    return { cfg: plaw, defaultAgent: agents }
  }, { persist: true })

  useEffect(() => {
    if (data) { setCfg(data.cfg); setDefaultAgent(data.defaultAgent) }
  }, [data])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg || agentsLoading) return <FormSkeleton sections={3} what="settings" />

  const onPickDefault = async (value: string) => {
    const name = await ensureBindableAgentName(value, discovered)
    const prev = defaultAgent
    setDefaultAgent(name)
    api.setDefaultAgent(name).catch((e) => {
      setDefaultAgent(prev)
      notify(`Couldn't set the default agent: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  const patch = (key: string, value: unknown, onSaved: () => void, label?: string) => {
    const prev = cfg[key]
    setCfg((c) => ({ ...c, [key]: value }))
    api.patchConfig(`agent.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, [key]: prev }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  const selfQa = (cfg.self_qa ?? {}) as Record<string, unknown>
  const patchSelfQa = (key: string, value: unknown, onSaved: () => void, label?: string) => {
    const prev = selfQa[key]
    setCfg((c) => ({ ...c, self_qa: { ...((c?.self_qa ?? {}) as object), [key]: value } }))
    api.patchConfig(`agent.self_qa.${key}`, value).then(onSaved).catch((e) => {
      setCfg((c) => ({ ...c, self_qa: { ...((c?.self_qa ?? {}) as object), [key]: prev } }))
      notify(`Couldn't save ${label ?? key}: ${String((e as Error)?.message || e)}`, 'error')
    })
  }

  return (
    <div>
      <PanelHeader title="Agent defaults" hint="The default agent for new sessions and how agents execute — approval, subagents, and advanced safety knobs. New sessions inherit these unless overridden." />

      <Section title="Default agent" hint="Which agent definition serves a new chat when none is chosen. Native agents and connected ACP-runtime agents are both selectable.">
        <DefaultAgentRow options={agentOptions} value={defaultAgent} onChange={onPickDefault} />
      </Section>

      <Section title="Defaults" hint="Baseline behavior for every session.">
        <RowGroup>
          <EnumRow label="Approval mode" hint="When the agent must ask before running a tool." cfg={cfg} field="approval_mode" patch={patch}
            options={[{ key: 'auto', label: 'Auto' }, { key: 'interactive', label: 'Ask each time' }, { key: 'trust_reads', label: 'Trust reads' }]} />
          <EnumRow label="Sandbox" hint="Sandbox mode for the ACP provider." cfg={cfg} field="sandbox" patch={patch}
            options={[{ key: 'auto', label: 'Auto' }, { key: 'off', label: 'Off' }]} />
          <ToggleRow label="YOLO mode" cfg={cfg} field="yolo" patch={patch}
            hint="Skip every tool-approval confirmation — overrides approval mode, applies immediately, and stays on until turned off (no expiry, unlike the chat YOLO pill). Only inside a sandbox or for trusted automation." danger
            confirmOn={(next) => next ? {
              title: 'Enable YOLO mode?',
              body: 'Tool-approval confirmations will be skipped until you turn YOLO mode off.',
              confirmLabel: 'Enable YOLO mode',
              danger: true,
            } : undefined} />
        </RowGroup>
      </Section>

      <RunnersSection />

      <Section title="Subagents" hint="Limits for helper agents the main agent spawns.">
        <RowGroup>
          <NumberRow label="Max concurrent subagents" hint="0 = auto-size from this host's CPU and memory." cfg={cfg} field="max_subagents" patch={patch} min={0} max={16} />
          <NumberRow label="Max turns per subagent" cfg={cfg} field="subagent_max_turns" patch={patch} min={1} max={200} />
          <NumberRow label="Subagent timeout" cfg={cfg} field="subagent_timeout_secs" patch={patch} min={60} max={7200} suffix="s" />
          <NumberRow label="Min free memory to spawn" cfg={cfg} field="spawn_min_memory_gb" patch={patch} min={0} max={64} step={0.5} suffix="GB" />
          <StrListField label="Allowed working directories" hint="Roots a subagent may run in." cfg={cfg} field="subagent_cwd_allowed_roots" patch={patch} placeholder="Add path…" />
        </RowGroup>
      </Section>

      <Section title="Advanced" hint="Delegation, safety enforcement, and diagnostics.">
        <RowGroup>
          <ToggleRow label="Orchestrator skill" cfg={cfg} field="orchestrator_skill" patch={patch}
            hint="Enable agent delegation — loads the orchestrator skill with the agent roster." />
          <ToggleRow label="Concurrent ACP sessions" cfg={cfg} field="acp_concurrent_sessions" patch={patch}
            hint="Run multiple ACP chats on ONE backend process (multiplexing) instead of one process per session. Only takes effect for backends that support session interleaving." />
          <ToggleRow label="Unattended runs need a verified adapter" cfg={cfg} field="unattended_requires_verified_adapter" patch={patch}
            hint="Refuse an unattended spawn — a cron fire, a loop-cycle worker, a subagent, the background session, an inbox or side sweep, a channel delivery, or a trigger dispatch — onto a runner whose ACP adapter has no verified provenance: an npx fetch-at-launch, an adapter that changed since it was provisioned, or a runner with no catalog row. Interactive chat is never gated. See each runner's adapter state under Runners." />
          <EnumRow label="Log level" hint="Backend logger level (overridden by --verbose)." cfg={cfg} field="log_level" patch={patch}
            options={[{ key: 'DEBUG', label: 'Debug' }, { key: 'INFO', label: 'Info' }, { key: 'WARNING', label: 'Warning' }, { key: 'ERROR', label: 'Error' }]} />
          <NumberRow label="Soft-stop budget" cfg={cfg} field="soft_stop_budget_secs" patch={patch} min={0.5} max={60} step={0.5} suffix="s"
            hint="Seconds to wait for a cooperative cancel before hard-killing a session." />
          <NumberRow label="Runner health check interval" cfg={cfg} field="runner_health_check_secs" patch={patch} min={60} max={86400} step={60} suffix="s"
            hint="How long a runner's measured health stays current. Past this, its row under Runners is marked check overdue rather than presenting an old reading as the present state. Nothing is probed automatically — use Re-check runners." />
          <NumberRow label="Runner idle release" cfg={cfg} field="runner_idle_release_secs" patch={patch} min={60} max={86400} step={60} suffix="s"
            hint="How long a session may hold an agent runner without using it. Past this the hold is released and the runner reads as free under Runners — so a session that went quiet, or a gateway that was killed, cannot leave a runner looking permanently taken. The session itself is untouched." />
          <ToggleRow label="Durable worker sessions" cfg={cfg} field="durable_sessions" patch={patch}
            hint={persistClaim(cfg.durable_sessions, tmuxAvailable)
              ? "Confirmed active: workers run inside tmux on Gideon's own socket so their shell outlives the gateway."
              : tmuxAvailable === false
                ? 'Requires tmux; this setting has no effect while tmux is unavailable.'
                : "Run workers inside tmux on Gideon's own socket when tmux availability is confirmed."} />
        </RowGroup>
        {
}
      </Section>

      <Section title="Self-QA companion" hint="Watch a repository and QA each user-impacting commit as a user would — driving the real UI, then filing an Inbox item and a Task when a scenario fails. Off by default: it spends model calls and drives your browser unattended.">
        <RowGroup>
          <ToggleRow label="Enable the companion" cfg={selfQa} field="enabled" patch={patchSelfQa}
            hint="Off means the commit watcher stays idle. A commit only gets a scenario when the change could actually be noticed by a user — test-only and docs-only commits are recorded as skips, with the reason, and cost nothing." />
          <TextRow label="Watched repository" cfg={selfQa} field="watched_repo" patch={patchSelfQa}
            placeholder="/path/to/your/repo"
            hint="Absolute path to the git repository whose new commits trigger a QA run. Empty leaves the watcher idle even when the companion is enabled." />
          <NumberRow label="Max scenarios per run" cfg={selfQa} field="max_scenarios_per_fire" patch={patchSelfQa} min={1} max={20}
            hint="Ceiling on scenarios generated from one push. Every commit still gets a verdict; this bounds how many browser sessions one push can start." />
          <ToggleRow label="Propose fix branches" cfg={selfQa} field="fix_branch_enabled" patch={patchSelfQa} danger
            hint="On a confirmed failure, open a gideon/selfqa-<sha> branch carrying a proposed diff. Never merged and never pushed — the branch name lands in the Task for you to review."
            confirmOn={(next) => next ? {
              title: 'Let the companion propose fix branches?',
              body: 'On a confirmed failure, the companion can create a local branch with a proposed fix for you to review.',
              confirmLabel: 'Allow fix branches',
              danger: true,
            } : undefined} />
        </RowGroup>
      </Section>
    </div>
  )
}

function DefaultAgentRow({ options, value, onChange }: { options: AgentOption[]; value: string; onChange: (v: string) => void }) {
  const [saved, setSaved] = useState(false)
  const opts = options.some((o) => o.value === value) || !value
    ? options
    : [{ value, label: value, group: 'Current' }, ...options]
  return (
    <RowGroup>
      <Row label="Default agent" hint="Used for every new session.">
        <div className="flex items-center gap-2">
          <SavedToast show={saved} />
          <div className="w-56">
            <Combobox options={opts} value={value} placeholder="Select an agent…" emptyText="No agents"
              onChange={(v) => { onChange(v); setSaved(true); window.setTimeout(() => setSaved(false), 1500) }} />
          </div>
        </div>
      </Row>
    </RowGroup>
  )
}


function RunnersSection() {
  const [rows, setRows] = useState<RunnerRow[] | null>(null)
  const [err, setErr] = useState<unknown>(null)
  const [probing, setProbing] = useState(false)

  const load = useCallback((probe: boolean) => {
    if (probe) setProbing(true)
    api.agentRunners(probe)
      .then((r) => { setRows(r); setErr(null) })
      .catch((e) => setErr(e))
      .finally(() => setProbing(false))
  }, [])

  useEffect(() => { load(false) }, [load])

  return (
    <Section title="Runners"
      hint="External agent CLIs this install can drive. Health is measured by asking each CLI for its own version — nothing else is spawned."
      right={<Button size="xs" variant="secondary" loading={probing} onClick={() => load(true)}>Re-check runners</Button>}>
      {!rows && err ? <LoadError what="runners" error={err} onRetry={() => load(false)} />
        : !rows ? <FormSkeleton sections={1} rows={4} title={false} what="runners" />
        : rows.length === 0 ? (
          <p data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low">
            No runners in the catalog. Drop a definition into <code className="font-mono">runners/&lt;id&gt;.json</code> under your Gideon home to add one.
          </p>
        ) : (
          <ul className="divide-y divide-outline-variant overflow-hidden rounded-lg bg-surface-container">
            {rows.map((r) => <RunnerRowItem key={r.id} row={r} />)}
          </ul>
        )}
    </Section>
  )
}

function Chip({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: 'neutral' | 'ok' | 'bad' }) {
  const base = 'inline-flex items-center rounded-pill px-2.5 py-1'
  if (tone === 'ok') return <span data-type="caption" className={base} style={accentChip}>{children}</span>
  const toneCx = tone === 'bad' ? 'text-danger' : 'text-on-surface'
  return <span data-type="caption" className={`${base} bg-surface-high ${toneCx}`}>{children}</span>
}

function RunnerRowItem({ row }: { row: RunnerRow }) {
  const h = row.health
  const caps = row.capabilities
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span data-type="body-m" className="text-on-surface">{row.display_name}</span>
        <span data-type="caption" className="font-mono text-on-surface-low">{row.runtime_id}</span>
        {row.source === 'user' && <Chip>your definition</Chip>}
        {h === null ? <Chip>never probed</Chip> : h.ok ? <Chip tone="ok">healthy</Chip> : <Chip tone="bad">unhealthy</Chip>}
        {
}
        {row.health_stale === true && <Chip>check overdue</Chip>}
        {
}
        {row.lease !== null && <Chip>held by {row.lease.holder}</Chip>}
      </div>
      {
}
      {row.lease !== null && (
        <div data-type="caption" className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-on-surface-low">
          <span>held for {row.lease.age_secs}s</span>
          <span>released in {row.lease.expires_in_secs}s if idle</span>
        </div>
      )}

      {
}
      {h !== null && (
        <div data-type="caption" className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-on-surface-low">
          <span>{h.version ? `v${h.version}` : 'version unknown'}</span>
          <span>{h.latency_ms === null ? 'latency unknown' : `${h.latency_ms} ms`}</span>
          <span>last {h.probe} probe {new Date(h.checked_at).toLocaleString()}</span>
        </div>
      )}
      {
}
      {h !== null && !h.ok && h.error && (
        <FieldError className="mt-1.5 whitespace-pre-wrap break-words font-mono">{h.error}</FieldError>
      )}

      {
}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {caps === null ? (
          <span data-type="caption" className="text-on-surface-low">Capabilities unknown — no handshake recorded yet.</span>
        ) : (
          <>
            {caps.permission_modes.map((m) => <Chip key={`m-${m}`}>{m}</Chip>)}
            {caps.efforts.map((e) => <Chip key={`e-${e}`}>effort: {e}</Chip>)}
            {caps.models.length > 0 && <Chip>{caps.models.length} model{caps.models.length === 1 ? '' : 's'}</Chip>}
            {caps.permission_modes.length === 0 && caps.efforts.length === 0 && caps.models.length === 0 && (
              <span data-type="caption" className="text-on-surface-low">Handshake recorded no capabilities.</span>
            )}
          </>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Chip tone={row.adapter.verified ? 'ok' : 'bad'}>adapter {row.adapter.state}</Chip>
        <span data-type="caption" className="text-on-surface-low">{row.adapter.detail}</span>
      </div>
    </li>
  )
}

function useSavedFlash(): [boolean, () => void] {
  const [saved, setSaved] = useState(false)
  return [saved, () => { setSaved(true); window.setTimeout(() => setSaved(false), 1500) }]
}

function TextRow({ label, hint, cfg, field, patch, placeholder }: {
  label: string; hint?: string; cfg: AgentCfg; field: string
  patch: (k: string, v: unknown, cb: () => void, label?: string) => void
  placeholder?: string
}) {
  const stored = String(cfg[field] ?? '')
  const [draft, setDraft] = useState(stored)
  const [saved, flash] = useSavedFlash()
  useEffect(() => { setDraft(stored) }, [stored])
  const dirty = draft !== stored
  const save = () => patch(field, draft.trim(), flash, label)
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-s">
        <SavedToast show={saved} />
        <div className="flex-1" style={{ maxWidth: 280 }}>
          <TextInput value={draft} onChange={setDraft} placeholder={placeholder} ariaLabel={label} size="sm"
            onKeyDown={(e) => { if (e.key === 'Enter' && dirty) save() }} />
        </div>
        <Button size="sm" variant={dirty ? 'primary' : 'secondary'} ariaLabel={`Save: ${label}`}
          disabled={!dirty} disabledReason={!dirty ? 'No changes to save' : undefined} onClick={save}>
          Save
        </Button>
      </div>
    </Row>
  )
}

function EnumRow({ label, hint, cfg, field, patch, options }: {
  label: string; hint?: string; cfg: AgentCfg; field: string; patch: (k: string, v: unknown, cb: () => void) => void
  options: { key: string; label: string }[]
}) {
  const [saved, flash] = useSavedFlash()
  const value = String(cfg[field] ?? options[0].key)
  const opts = options.some((o) => o.key === value) ? options : [{ key: value, label: value }, ...options]
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <SegPills ariaLabel={label} value={value} onChange={(v) => patch(field, v, flash)} options={opts} />
      </div>
    </Row>
  )
}

function NumberRow({ label, hint, cfg, field, patch, min, max, step, suffix }: {
  label: string; hint?: string; cfg: AgentCfg; field: string; patch: (k: string, v: unknown, cb: () => void) => void
  min?: number; max?: number; step?: number; suffix?: string
}) {
  const [saved, flash] = useSavedFlash()
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        <NumberField value={Number(cfg[field] ?? min ?? 0)} min={min} max={max} step={step}
          onChange={(n) => patch(field, n, flash)} ariaLabel={label} />
        {suffix && <span data-type="caption" className="w-6 text-on-surface-low">{suffix}</span>}
      </div>
    </Row>
  )
}
