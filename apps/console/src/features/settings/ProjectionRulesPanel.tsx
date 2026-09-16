import { useEffect, useState } from 'react'
import { unavailableWhen } from '../../shared/ui/unavailable'
import { Scissors, Plus, X, AlertTriangle, Gauge, RotateCcw } from 'lucide-react'
import { api, type ProjectionRule, type ProjectionStrategy, type ToolsSavings } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { InlineError } from '../../shared/ui/InlineError'
import { ListSkeleton } from '../../shared/ui/ListScaffold'
import { NumberField, TextInput } from '../../shared/ui/forms'
import { PanelHeader, Section } from './settingsUI'

const STRATEGIES: { id: ProjectionStrategy; label: string; blurb: string }[] = [
  { id: 'log', label: 'Log', blurb: 'keep head + error/warning lines + tail' },
  { id: 'test', label: 'Test', blurb: 'keep failures + the summary line' },
  { id: 'diff', label: 'Diff', blurb: 'keep changed hunks + a +N/−M stat' },
  { id: 'json', label: 'JSON', blurb: 'keep field schema + first/last items' },
  { id: 'csv', label: 'CSV', blurb: 'keep the header + first/last rows' },
  { id: 'code', label: 'Code', blurb: 'keep signatures + docstrings + line map' },
]

export function ProjectionRulesPanel() {
  const { data: rules, error: loadErr, refresh } = useQuery(
    'settings:projection-rules', () => api.projectionRules(),
    { persist: true },
  )
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  const save = async (next: ProjectionRule[]) => {
    setBusy(true); setErr('')
    try { await api.setProjectionRules(next); refresh() }
    catch (e) { setErr(e instanceof Error ? e.message : 'Failed to save') }
    finally { setBusy(false) }
  }

  const list = rules ?? []

  return (
    <div>
      <PanelHeader title="Tool-output projection"
        hint="Teach Gideon how to keep the salient slice of a large tool output — so a verbose result costs a preview, not the whole context window, while the full raw stays recoverable on demand." />

      <SavingsCard />

      <Section title="Custom rules"
        hint="A rule maps a content marker (regex, matched against the start of the output) to a projection strategy. Use it for a tool whose big output the builtin sniffer treats as generic (a blunt head/tail cut) — e.g. a domain-specific log or dump. Rules are checked before the builtin sniff.">
        <div className="flex flex-col gap-2">
          {list.map((r, i) => (
            <RuleRow key={i} rule={r} disabled={busy}
              onChange={(next) => save(list.map((x, j) => (j === i ? next : x)))}
              onRemove={() => save(list.filter((_, j) => j !== i))} />
          ))}
          {
}
          {rules === undefined && loadErr ? (
            <InlineError icon>
              <span className="flex-1">Couldn't load your projection rules{(loadErr as Error)?.message ? `: ${(loadErr as Error).message}` : '.'}</span>
              <Button variant="secondary" size="sm" onClick={refresh}><RotateCcw size={14} /> Retry</Button>
            </InlineError>
          ) : rules === undefined ? (
            <ListSkeleton rows={2} what="custom rules" />
          ) : list.length === 0 ? (
            <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-3 text-on-surface-low">
              No custom rules — the builtin projectors handle logs, diffs, JSON, test output, CSV, and code automatically, and a builtin rule pack recognises common command output (git, pytest, npm, docker…). Add a rule only for a tool whose large output isn't recognised.
            </div>
          ) : null}
          <AddRule disabled={busy} onAdd={(r) => save([...list, r])} />
          {err && <div data-type="body-s" className="flex items-center gap-1.5 text-danger"><AlertTriangle size={13} /> {err}</div>}
        </div>
      </Section>
    </div>
  )
}

/** Exported for test: the breakdown's derivations (zero filter, savings ordering, the
 *  more-than-one gate, chars→tokens conversion) are only observable by rendering the card against a
 *  stubbed summary — jsdom reports every box as 0, so none of it is measurable from layout. */
export function SavingsCard() {
  const { data } = useQuery<ToolsSavings>(
    'settings:tools-savings', () => api.toolsSavings(), { persist: true },
  )
  if (!data || data.saved_chars <= 0) return null
  const fmt = (n: number) => n.toLocaleString()
  const breakdown = Object.entries(data.by_compressor ?? {})
    .filter(([, chars]) => chars > 0)
    .sort((a, b) => b[1] - a[1])
  return (
    <div className="mb-4 flex items-start gap-3 rounded-lg bg-surface-container px-3 py-3">
      <Gauge size={16} className="mt-0.5 shrink-0 text-primary" />
      <div data-type="body-s" className="min-w-0">
        <div className="text-on-surface">
          TokenJuice saved <span className="fw-500">~{fmt(data.saved_tokens_estimated)}</span> tokens
          {' '}across {fmt(data.projection_count)} projected result{data.projection_count === 1 ? '' : 's'}
          {data.top_compressor ? <> — top compressor: <span className="font-mono">{data.top_compressor}</span></> : null}.
        </div>
        {breakdown.length > 1 && (
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-on-surface-var">
            {breakdown.map(([name, chars]) => (
              <span key={name}>
                <span className="font-mono">{name}</span>{' '}
                <span className="tabular-nums">~{fmt(Math.round(chars / 4))}</span>
              </span>
            ))}
          </div>
        )}
        <div className="mt-0.5 text-on-surface-low">
          Estimated ({fmt(data.saved_chars)} chars, ~4 chars/token). The full raw of every projected result stays recoverable via <span className="font-mono">tool_result_get</span>.
        </div>
      </div>
    </div>
  )
}

function StrategyPicker({ value, disabled, onChange, forRule }: {
  value: ProjectionStrategy; disabled?: boolean; onChange: (s: ProjectionStrategy) => void
  forRule?: string
}) {
  return (
    <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as ProjectionStrategy)}
      aria-label={forRule ? `Strategy for ${forRule}` : 'Strategy for the new rule'}
      data-type="body-s" className="min-w-0 max-w-full h-9 rounded-md bg-surface px-2 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary">
      {STRATEGIES.map((s) => <option key={s.id} value={s.id}>{s.label} — {s.blurb}</option>)}
    </select>
  )
}

function RuleRow({ rule, disabled, onChange, onRemove }: {
  rule: ProjectionRule; disabled?: boolean; onChange: (r: ProjectionRule) => void; onRemove: () => void
}) {
  const hasOps = Boolean(rule.head || rule.tail || rule.keep || rule.skip || rule.count)
  const [showOps, setShowOps] = useState(hasOps)
  const [draft, setDraft] = useState<ProjectionRule | null>(null)
  const shown = draft ?? rule
  useEffect(() => {
    if (draft && JSON.stringify(draft) === JSON.stringify(rule)) setDraft(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rule])
  const edit = (patch: Partial<ProjectionRule>) => setDraft({ ...shown, ...patch })
  const commit = () => {
    if (!draft) return
    if (JSON.stringify(draft) === JSON.stringify(rule)) { setDraft(null); return }
    onChange(draft)
  }
  const commitOnEnter = (e: React.KeyboardEvent) => { if (e.key === 'Enter') commit() }
  const inputCls = 'h-9 rounded-md bg-surface px-2 font-mono text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary'
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-surface-container px-3 py-2.5"
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) commit() }}>
      {
}
      <div className="flex flex-wrap items-center gap-2">
        <Scissors size={13} className="shrink-0 text-on-surface-low" />
        <input value={shown.name} disabled={disabled} placeholder="rule name"
          aria-label="Rule name"
          onChange={(e) => edit({ name: e.target.value })} onKeyDown={commitOnEnter}
          data-type="body-s" className="min-w-40 flex-1 h-9 rounded-md bg-surface px-2 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        {
}
        {
}
        <div className="flex min-w-0 items-center gap-2">
          <StrategyPicker value={shown.strategy} disabled={disabled} forRule={shown.name}
            onChange={(s) => edit({ strategy: s })} />
          {
}
          <button type="button" disabled={disabled} onClick={onRemove}
            aria-label={rule.name ? `Remove rule ${rule.name}` : 'Remove rule'}
            className="ml-auto shrink-0 rounded-md p-1 text-on-surface-low hover:bg-surface-high hover:text-on-surface"><X size={15} /></button>
        </div>
      </div>
      <input value={shown.match_regex} disabled={disabled} spellCheck={false} placeholder="match regex, e.g. ^\[MYAPP\]"
        aria-label={rule.name ? `Match regex for ${rule.name}` : 'Match regex'}
        onChange={(e) => edit({ match_regex: e.target.value })} onKeyDown={commitOnEnter}
        data-type="body-s" className={inputCls} />
      {
}
      {showOps ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
          <div data-type="caption" className="flex flex-col gap-1 text-on-surface-low">head lines
            <NumberField value={shown.head ?? 0} min={0} width="w-full" ariaLabel="Keep head lines"
              onChange={(n) => edit({ head: n })} />
          </div>
          <div data-type="caption" className="flex flex-col gap-1 text-on-surface-low">tail lines
            <NumberField value={shown.tail ?? 0} min={0} width="w-full" ariaLabel="Keep tail lines"
              onChange={(n) => edit({ tail: n })} />
          </div>
          <div data-type="caption" className="flex flex-col gap-1 text-on-surface-low">keep matching
            <TextInput value={shown.keep ?? ''} size="sm" mono placeholder="regex" ariaLabel="Keep lines matching regex"
              onChange={(v) => edit({ keep: v })} />
          </div>
          <div data-type="caption" className="flex flex-col gap-1 text-on-surface-low">skip matching
            <TextInput value={shown.skip ?? ''} size="sm" mono placeholder="regex" ariaLabel="Skip lines matching regex"
              onChange={(v) => edit({ skip: v })} />
          </div>
          <div data-type="caption" className="flex flex-col gap-1 text-on-surface-low">fold matching
            <TextInput value={shown.count ?? ''} size="sm" mono placeholder="regex" ariaLabel="Fold lines matching regex"
              onChange={(v) => edit({ count: v })} />
          </div>
        </div>
      ) : (
        <div className="self-start">
          <Button variant="ghost" size="xs" disabled={disabled} onClick={() => setShowOps(true)}>
            + line operations (head/tail window, keep/skip/fold filters)
          </Button>
        </div>
      )}
    </div>
  )
}

function AddRule({ disabled, onAdd }: { disabled?: boolean; onAdd: (r: ProjectionRule) => void }) {
  const [name, setName] = useState('')
  const [rx, setRx] = useState('')
  const [strat, setStrat] = useState<ProjectionStrategy>('log')
  const add = () => {
    if (!rx.trim()) return
    onAdd({ name: name.trim(), match_regex: rx.trim(), strategy: strat })
    setName(''); setRx(''); setStrat('log')
  }
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-dashed border-outline-variant/50 px-3 py-2.5">
      {
}
      <div className="flex flex-wrap items-center gap-2">
        <Plus size={13} className="shrink-0 text-on-surface-low" />
        <input value={name} disabled={disabled} placeholder="new rule name"
          aria-label="New rule name"
          onChange={(e) => setName(e.target.value)}
          data-type="body-s" className="min-w-40 flex-1 h-9 rounded-md bg-surface px-2 text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        <StrategyPicker value={strat} disabled={disabled} onChange={setStrat} />
      </div>
      <div className="flex items-center gap-2">
        <input value={rx} disabled={disabled} spellCheck={false} placeholder="match regex, e.g. ^\[MYAPP\]"
          aria-label="Match regex for the new rule"
          onChange={(e) => setRx(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') add() }}
          data-type="body-s" className="min-w-0 flex-1 h-9 rounded-md bg-surface px-2 font-mono text-on-surface placeholder:text-on-surface-low outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
        <button type="button" onClick={add} data-type="body-s"
          {...unavailableWhen(!rx.trim(), 'Enter a pattern first', { busy: disabled })}
          className="shrink-0 h-9 rounded-md bg-primary px-3 text-on-primary disabled:opacity-40 aria-disabled:opacity-40 aria-disabled:cursor-not-allowed">Add rule</button>
      </div>
    </div>
  )
}
