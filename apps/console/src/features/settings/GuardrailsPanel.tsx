import { useEffect, useMemo, useState } from 'react'
import { api, type AutonomyLadder, type AutonomyReversal, type AutonomyType, type CallerHealth, type ProviderHealth } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { PanelHeader, Section, RowGroup, Row, Field, SegPills, Toggle, SavedToast } from './settingsUI'
import { NumberField } from '../../shared/ui/forms'
import { Button } from '../../shared/ui/Button'
import { RungChip } from '../../shared/ui/RungChip'
import { rungMeta } from '../../shared/data/rungs'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'

type GuardrailsCfg = {
  budgets?: { max_tokens_per_run?: number; max_tokens_per_day?: number; max_dollars_per_day?: number }
  breaker?: { failure_threshold?: number; recovery_secs?: number }
  loop_breaker?: { circuit_threshold?: number }
  scan_mode?: string
}

export function GuardrailsPanel() {
  const [cfg, setCfg] = useState<GuardrailsCfg | null>(null)

  const { data, error: loadErr, refresh } = useQuery('settings:guardrails', () =>
    api.gideonConfig().then((c) => (c.guardrails ?? {}) as GuardrailsCfg),
    { persist: true },
  )
  const { data: dailySpend, refresh: refreshDailySpend } = useQuery('triggers:budget', () => api.triggerBudget())
  useEffect(() => { if (data) setCfg(data) }, [data])

  if (!data && loadErr) return <LoadError what="settings" error={loadErr} onRetry={refresh} />
  if (!data || !cfg) return <FormSkeleton sections={3} what="settings" />

  const patchNum = (path: string, value: number, label?: string) =>
    api.patchConfig(`guardrails.${path}`, value).then(() => true).catch((e) => {
      notify(`Couldn't save ${label ?? path}: ${String((e as Error)?.message || e)}`, 'error')
      return false
    })

  return (
    <div>
      <PanelHeader title="Guardrails" hint="The personal safety floor for unattended work — a daily spend ceiling, an outbound secret scan, provider circuit breakers, and a kill switch. The tool-loop ceiling also applies to interactive turns." />

      <IncidentSection />

      <Section title="Daily budget" hint="Cap what your automations spend in a day. At the ceiling, further unattended runs are skipped (a cron fire is paused, a subagent spawn refused) and resume automatically the next day. 0 = unlimited.">
        {dailySpend && <div role="status" data-type="body-s" className="mb-m rounded-lg bg-surface-container px-m py-3 text-on-surface-var">
          {dailySpend.paused ? 'Paused by daily budget' : dailySpend.status === 'warn' ? 'Approaching daily budget' : 'Daily automation budget available'}
          {' · '}{dailySpend.tokens.toLocaleString()} tokens and ${dailySpend.dollars.toFixed(4)} recorded today.
          {dailySpend.paused && <> Resumes automatically {new Date(dailySpend.resumes_at).toLocaleString()}.</>}
          <Button variant="ghost" size="xs" onClick={refreshDailySpend}>Refresh spend</Button>
        </div>}
        <RowGroup>
          <NumberRow label="Max tokens / day" hint="Across every trigger. 0 = unlimited."
            value={cfg.budgets?.max_tokens_per_day ?? 0} min={0} step={1000}
            onSave={(v) => { setCfg((c) => ({ ...c, budgets: { ...c?.budgets, max_tokens_per_day: v } })); return patchNum('budgets.max_tokens_per_day', v, 'Max tokens / day') }} />
          <NumberRow label="Max dollars / day" hint="Estimated from per-model pricing. 0 = unlimited."
            value={cfg.budgets?.max_dollars_per_day ?? 0} min={0} step={1} dollars
            onSave={(v) => { setCfg((c) => ({ ...c, budgets: { ...c?.budgets, max_dollars_per_day: v } })); return patchNum('budgets.max_dollars_per_day', v, 'Max dollars / day') }} />
          <NumberRow label="Max tokens / run" hint="Per single unattended run (a goal-loop cycle, a cron fire). 0 = unlimited."
            value={cfg.budgets?.max_tokens_per_run ?? 0} min={0} step={1000}
            onSave={(v) => { setCfg((c) => ({ ...c, budgets: { ...c?.budgets, max_tokens_per_run: v } })); return patchNum('budgets.max_tokens_per_run', v, 'Max tokens / run') }} />
        </RowGroup>
      </Section>

      <Section title="Outbound scan" hint="How a prompt bound for a REMOTE model provider is handled when it contains secrets or PII. Local models always warn (their content never leaves your machine).">
        <RowGroup>
          <Field label="Scan mode" hint="warn = log & send · redact = substitute & send · block = refuse the call.">
            <SegPills ariaLabel="Scan mode" value={String(cfg.scan_mode ?? 'redact')}
              onChange={(v) => { setCfg((c) => ({ ...c, scan_mode: v })); api.patchConfig('guardrails.scan_mode', v).catch((e) => notify(`Couldn't save scan mode: ${String((e as Error)?.message || e)}`, 'error')) }}
              options={[{ key: 'warn', label: 'Warn' }, { key: 'redact', label: 'Redact' }, { key: 'block', label: 'Block' }]} />
          </Field>
        </RowGroup>
      </Section>

      <Section title="Provider circuit breaker" hint="Per-provider fail-fast: after N consecutive failures a provider's breaker opens, so unattended runs fail in microseconds during an outage instead of stacking timeouts.">
        <RowGroup>
          <NumberRow label="Failure threshold" hint="Consecutive failures before the breaker opens."
            value={cfg.breaker?.failure_threshold ?? 5} min={1} step={1}
            onSave={(v) => { setCfg((c) => ({ ...c, breaker: { ...c?.breaker, failure_threshold: v } })); return patchNum('breaker.failure_threshold', v, 'Failure threshold') }} />
          <NumberRow label="Recovery seconds" hint="How long an open breaker waits before a half-open probe."
            value={cfg.breaker?.recovery_secs ?? 30} min={0} step={5}
            onSave={(v) => { setCfg((c) => ({ ...c, breaker: { ...c?.breaker, recovery_secs: v } })); return patchNum('breaker.recovery_secs', v, 'Recovery seconds') }} />
        </RowGroup>
      </Section>

      <Section title="Tool-loop breaker" hint="Abort a turn when its total tool failures exceed this ceiling. Applies to native and ACP turns; changes take effect on the next run.">
        <RowGroup>
          <NumberRow label="Tool failure ceiling" hint="Minimum 1. Successful calls do not erase the run-wide failure count."
            value={cfg.loop_breaker?.circuit_threshold ?? 30} min={1} step={1}
            onSave={async (v) => {
              const saved = await patchNum('loop_breaker.circuit_threshold', v, 'Tool failure ceiling')
              if (saved) setCfg((c) => ({ ...c, loop_breaker: { ...c?.loop_breaker, circuit_threshold: v } }))
              return saved
            }} />
        </RowGroup>
      </Section>

      <AutonomyLadderSection />

      <ProviderHealthSection />
    </div>
  )
}

function AutonomyLadderSection() {
  const { data: ladder, error: loadErr, refresh } = useQuery('autonomy:ladder', () => api.autonomyLadder(), { persist: true })
  const [busy, setBusy] = useState('')
  const reload = () => { invalidateKeys('autonomy:ladder'); refresh() }

  const types = useMemo(() => {
    const order = ladder?.rungs ?? []
    return [...(ladder?.types ?? [])].sort((a, b) =>
      Number(b.eligible) - Number(a.eligible) ||
      order.indexOf(a.resolved_rung) - order.indexOf(b.resolved_rung) ||
      a.key.localeCompare(b.key),
    )
  }, [ladder])

  const promote = async (t: AutonomyType) => {
    setBusy(t.key)
    try {
      const r = await api.autonomyGrant(t.key, t.next_rung)
      notify(`${t.key} now ${rungMeta(r.rung, ladder ?? null).label}.`, 'success')
      reload()
    } catch (e) {
      notify(`Couldn't promote ${t.key}: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy('') }
  }
  const handBack = async (t: AutonomyType) => {
    setBusy(t.key)
    try {
      await api.autonomyDemote(t.key)
      notify(`${t.key} is back at ${rungMeta(t.floor, ladder ?? null).label}.`, 'success')
      reload()
    } catch (e) {
      notify(`Couldn't change ${t.key}: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy('') }
  }

  return (
    <Section title="Earned autonomy" hint="Every automated action starts at the rung it was declared with and can only climb when you say so. A single rejection, thumbs-down or undo drops it back immediately and starts a cooldown.">
      {!ladder && loadErr ? (
        <LoadError what="autonomy ladder" error={loadErr} onRetry={reload} />
      ) : !ladder ? (
        <div data-type="body-s" className="rounded-lg bg-surface-container px-4 py-3 text-on-surface-low">Loading…</div>
      ) : (
        <div className="flex flex-col gap-l">
          {ladder.incident_active && (
            <div role="alert" data-type="body-s" className="rounded-lg px-4 py-3 bg-error/10 ring-1 ring-error/40 text-on-surface-var">
              Incident mode is active, so nothing runs above “asks first” — a granted rung shows as held until you resume.
            </div>
          )}
          <RowGroup>
            {types.map((t) => (
              <div key={t.key} className="flex items-start justify-between gap-l border-b border-outline-variant/30 py-3 last:border-0">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span data-type="body-s" className="text-on-surface">{t.key}</span>
                    <RungChip type={t} ladder={ladder} />
                    {t.leaves_machine && (
                      <span data-type="caption" className="text-on-surface-low" title="Its effect is visible outside this machine, so a track record can never propose full autonomy for it.">leaves this machine</span>
                    )}
                  </div>
                  <div data-type="body-s" className="mt-0.5 text-on-surface-low">{t.authority}</div>
                  <div data-type="caption" className="mt-0.5 text-on-surface-low">{t.record}</div>
                  {t.demotions.length > 0 && (
                    <div data-type="caption" className="mt-0.5 text-on-surface-low">
                      Last demoted {t.demotions[t.demotions.length - 1].at.slice(0, 10)} — {t.demotions[t.demotions.length - 1].cause}
                      {t.demotions.length > 1 && ` (${t.demotions.length} demotions on record)`}
                    </div>
                  )}
                </div>
                <div className="shrink-0 flex items-center gap-2">
                  {t.eligible && t.next_rung && (
                    <Button size="xs" variant="secondary" loading={busy === t.key} onClick={() => promote(t)}>
                      Promote so it {rungMeta(t.next_rung, ladder).label}
                    </Button>
                  )}
                  {t.granted_at && (
                    <Button size="xs" variant="ghost" loading={busy === t.key} onClick={() => handBack(t)}
                      title={`Drop ${t.key} back to ${rungMeta(t.floor, ladder).label} and start its cooldown.`}>
                      Hand back
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </RowGroup>
          <UndoList ladder={ladder} onChange={reload} />
        </div>
      )}
    </Section>
  )
}

function UndoList({ ladder, onChange }: { ladder: AutonomyLadder; onChange: () => void }) {
  const [busy, setBusy] = useState('')
  const pending = ladder.reversals.filter((r) => !r.reversed_at)

  const undo = async (r: AutonomyReversal) => {
    setBusy(r.id)
    try {
      await api.autonomyUndo(r.id)
      notify(`Undone. ${r.action_type} will ask again from now on.`, 'success')
    } catch (e) {
      notify(`Couldn't undo: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy(''); onChange() }
  }

  return (
    <div>
      <div data-type="caption" className="mb-s text-on-surface-low uppercase tracking-wide">Automatic actions you can still undo</div>
      <RowGroup>
        {pending.length === 0 ? (
          <div data-type="body-s" className="py-3 text-on-surface-low">Nothing is waiting to be undone — no action has run at the “runs with undo” rung yet.</div>
        ) : pending.map((r) => (
          <Row key={r.id} label={r.label || r.action_type}
            hint={`Ran ${r.created_at.slice(0, 16).replace('T', ' ')}. Undoing it also stops ${r.action_type} from doing this on its own.`}>
            <Button size="xs" variant="secondary" loading={busy === r.id} onClick={() => undo(r)}>Undo</Button>
          </Row>
        ))}
      </RowGroup>
    </div>
  )
}

function IncidentSection() {
  const [state, setState] = useState<{ active: boolean; reason: string; started_at: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const load = () => api.incident().then((s) => { setState(s); setLoadErr(null) }).catch(setLoadErr)
  useEffect(() => { load() }, [])

  const toggle = async (on: boolean) => {
    setBusy(true)
    try {
      if (on) setState(await api.incidentOn('Activated from Settings'))
      else { await api.incidentResume(); setState({ active: false, reason: '', started_at: '' }) }
    } catch (e) {
      notify(`Couldn't ${on ? 'activate' : 'resume'} incident mode: ${String((e as Error)?.message || e)}`, 'error')
    } finally { setBusy(false) }
  }

  return (
    <Section title="Incident kill switch" hint="Suspend ALL unattended work — cron jobs, hooks, event triggers, subagent spawns — at once. Interactive chat keeps working. Resume is explicit.">
      <div className={`rounded-lg px-4 py-1 ${state?.active ? 'bg-error/10 ring-1 ring-error/40' : 'bg-surface-container'}`}>
        <Row label={state?.active ? 'Incident mode is ACTIVE' : 'Incident mode'}
          hint={loadErr
            ? `Couldn't check whether incident mode is active: ${String((loadErr as Error)?.message || loadErr)}`
            : state?.active ? `Unattended work suspended${state.reason ? ` — ${state.reason}` : ''}. Turn off to resume.`
            : state ? 'Off — automation runs normally.'
            : 'Checking…'}>
          <Toggle on={Boolean(state?.active)} disabled={busy || !state} onChange={toggle} label="Incident mode" />
        </Row>
      </div>
    </Section>
  )
}

function ProviderHealthSection() {
  const [rows, setRows] = useState<ProviderHealth[] | null>(null)
  const [callers, setCallers] = useState<CallerHealth[]>([])
  const [loadErr, setLoadErr] = useState<unknown>(null)
  const refresh = () => api.modelsHealth().then((r) => { setRows(r.providers); setCallers(r.callers ?? []); setLoadErr(null) }).catch(setLoadErr)
  useEffect(() => { refresh() }, [])

  return (
    <Section title="Provider health" hint="Derived from the model-call audit — breaker state, latency, and recent failures per provider. No data leaves your machine.">
      <div className="rounded-lg bg-surface-container px-4 py-3">
        {loadErr ? (
          <div role="alert" data-type="body-s" className="text-on-surface-low">
            Couldn't check provider health: {String((loadErr as Error)?.message || loadErr)}
          </div>
        ) : rows === null ? (
          <div data-type="body-s" className="text-on-surface-low">Loading…</div>
        ) : rows.length === 0 ? (
          <div data-type="body-s" className="text-on-surface-low">No background model calls recorded yet.</div>
        ) : (
          <div className="flex flex-col gap-2">
            {rows.map((p) => <HealthRow key={p.name} p={p} />)}
          </div>
        )}
        {callers.length > 0 && (
          <div className="mt-3 border-t border-outline-variant/30 pt-3">
            <div data-type="caption" className="text-on-surface-low">By background caller</div>
            <div className="mt-1 flex flex-col gap-1">
              {callers.map((c) => <CallerRow key={c.name} c={c} />)}
            </div>
          </div>
        )}
      </div>
    </Section>
  )
}

/** Exported for test: one line per SUBSYSTEM that asked for a model call (G47).
 *  Answers the question the provider rows cannot — "is my expensive background pass alive?" */
export function CallerRow({ c }: { c: CallerHealth }) {
  const dead = c.calls > 0 && c.passed === 0
  const worstMode = Object.entries(c.failure_modes ?? {}).sort((a, b) => b[1] - a[1])[0]
  return (
    <div data-type="caption" className="flex items-baseline justify-between gap-l">
      <span className="min-w-0 truncate text-on-surface">{c.name.replace(/_/g, ' ')}</span>
      <span className="text-on-surface-low tabular-nums">
        {c.calls} calls · <span style={dead ? { color: 'var(--color-error)' } : undefined}>
          {c.pass_rate === null ? '—' : `${Math.round(c.pass_rate * 100)}% ok`}
        </span>
        {c.p90_ms > 0 && ` · p90 ${Math.round(c.p90_ms)}ms`}
        {dead && worstMode && ` · ${worstMode[0].replace(/_/g, ' ')}`}
      </span>
    </div>
  )
}

/** Exported for test: jsdom reports every box as 0, so the only way to pin this row's derived
 *  values (mode ordering, the p99 threshold) is to render the component directly. */
export function HealthRow({ p }: { p: ProviderHealth }) {
  const failureModes = Object.entries(p.failure_modes ?? {}).sort((a, b) => b[1] - a[1])
  const stateColor = p.breaker_state === 'open' ? 'var(--color-error)'
    : p.breaker_state === 'half_open' ? 'var(--color-warning)' : 'var(--color-success)'
  const stateLabel = p.breaker_state === 'open' ? 'Open' : p.breaker_state === 'half_open' ? 'Half-open' : 'Closed'
  return (
    <div className="flex items-center justify-between gap-l border-b border-outline-variant/30 py-2 last:border-0">
      <div className="min-w-0">
        <div data-type="body-s" className="flex items-center gap-2 text-on-surface">
          <span className="inline-block h-2 w-2 rounded-full" style={{ background: stateColor }} />
          <span className="truncate">{p.name}</span>
          <span className="text-on-surface-low">· {stateLabel}</span>
          {p.degraded && <span style={{ color: 'var(--color-warning)' }}>· degraded</span>}
        </div>
        <div data-type="caption" className="mt-0.5 text-on-surface-low">
          {p.calls} calls · {p.pass_rate === null ? '—' : `${Math.round(p.pass_rate * 100)}% ok`}
          {p.p90_ms > 0 && ` · p90 ${Math.round(p.p90_ms)}ms`}
          {p.p99_ms > 0 && p.p99_ms >= p.p90_ms * 1.5 && ` (p99 ${Math.round(p.p99_ms)}ms)`}
          {p.failed > 0 && ` · ${p.failed} failed`}
        </div>
        {failureModes.length > 0 && (
          <div className="mt-0.5 flex flex-wrap gap-1">
            {failureModes.map(([mode, n]) => (
              <span key={mode}
                data-type="caption" className="rounded-pill bg-surface-high px-2 py-0.5 text-on-surface-low tabular-nums">
                {mode.replace(/_/g, ' ')} ×{n}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function NumberRow({ label, hint, value, min, step, dollars, onSave }: {
  label: string; hint?: string; value: number; min: number; step: number; dollars?: boolean
  onSave: (v: number) => Promise<unknown>
}) {
  const [saved, setSaved] = useState(false)
  const commit = (n: number) => {
    if (n === value) return
    setSaved(false)
    onSave(n).then((result) => {
      if (result === false) return
      setSaved(true)
      window.setTimeout(() => setSaved(false), 1500)
    }).catch(() => setSaved(false))
  }
  return (
    <Row label={label} hint={hint}>
      <div className="flex items-center gap-2">
        <SavedToast show={saved} />
        {dollars && <span data-type="body-s" className="text-on-surface-low">$</span>}
        <NumberField value={value} min={min} step={step} onChange={commit} ariaLabel={label} />
      </div>
    </Row>
  )
}
