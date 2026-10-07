import { Select } from '../../shared/ui/forms'
import { useEffect, useState } from 'react'
import { AlertTriangle, Clock3, RefreshCw } from 'lucide-react'
import { api, ApiError, type HypermidRuntimeConfigValueWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { StatusPill } from '../settings/bento'
import { Row, RowGroup, Section, Toggle } from '../settings/settingsUI'
import { openRevisionedDraft, reapplyRevisionedDraft, rejectRevisionedDraft, reloadRevisionedDraft, type RevisionedDraft } from './configState'
import { AuthorityControl } from './AuthorityControl'

const MODES: Array<{ value: HypermidRuntimeConfigValueWire['mode']; label: string; detail: string }> = [
  { value: 'off', label: 'Off', detail: 'Hypermid is inactive. Stored state is preserved.' },
  { value: 'pass_through', label: 'Pass-through', detail: 'Observe lifecycle and health while Gideon remains the context authority.' },
  { value: 'shadow', label: 'Shadow', detail: 'Compare local projections without publishing, model calls, or durable writes.' },
  { value: 'primary', label: 'Primary', detail: 'Stage Primary eligibility at a safe turn boundary, then review the writer handoff below.' },
]

export function RuntimeConfig() {
  const config = useQuery('hypermid:config:runtime', () => api.hypermidRuntimeConfig())
  const [state, setState] = useState<RevisionedDraft<HypermidRuntimeConfigValueWire>>()
  const [saving, setSaving] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [authorityOwnsWrites, setAuthorityOwnsWrites] = useState(false)
  useEffect(() => {
    if (config.data && !state) setState(openRevisionedDraft(config.data.config, config.data.config_digest, config.data.policy_revision))
  }, [config.data, state])
  if (!config.data && config.error) return <LoadError what="Hypermid runtime configuration" error={config.error} onRetry={config.refresh} />
  if (!config.data || !state) return <FormSkeleton sections={3} rows={3} what="Hypermid runtime configuration" />
  const setDraft = (change: Partial<HypermidRuntimeConfigValueWire>) => setState((current) => current && ({ ...current, draft: { ...current.draft, ...change } }))
  const modeUnavailable = !config.data.editable || authorityOwnsWrites
  const modeReason = authorityOwnsWrites ? 'Rollback writer authority to Gideon before changing mode.' : !config.data.editable ? 'Runtime policy does not permit editing the mode.' : undefined
  const dirty = JSON.stringify(state.draft) !== JSON.stringify(state.base)
  const save = async () => {
    setSaving(true); setError(''); setNotice('')
    try {
      const result = await api.stageHypermidRuntimeConfig(state.draft, state.policy_revision || 0, state.revision)
      setState(openRevisionedDraft(result.config, result.config_digest, result.policy_revision))
      setNotice(result.status === 'staged' ? 'Change staged for the next safe turn boundary.' : 'Configuration is already current.')
      config.refresh()
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        try {
          const current = await api.hypermidRuntimeConfig()
          setState((previous) => previous && rejectRevisionedDraft(previous, current.config, current.config_digest,
            'Runtime configuration changed after you opened it. Your draft is preserved.', current.policy_revision))
        } catch { setError(caught.message) }
      } else setError(caught instanceof Error ? caught.message : 'The runtime change was refused.')
    } finally { setSaving(false) }
  }
  const feature = (key: keyof HypermidRuntimeConfigValueWire['features'], value: boolean) => setDraft({ features: { ...state.draft.features, [key]: value } })
  return <>
    <Section title="Runtime mode" hint="Mode controls Hypermid's authority. Strictness and optional work remain separate settings.">
      <RowGroup>
        <Row label="Effective source" hint={`Policy revision ${config.data.policy_revision}`}><StatusPill label={config.data.source.replaceAll('_', ' ')} tone="muted" /></Row>
        <Row label="Mode" hint={MODES.find((mode) => mode.value === state.draft.mode)?.detail}>
          <Select value={state.draft.mode} onChange={(value) => { if (modeUnavailable) return; setDraft({ mode: value as HypermidRuntimeConfigValueWire['mode'] }) }} ariaLabel="Hypermid runtime mode" className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-on-surface focus:outline-none focus:ring-2 focus:ring-primary aria-disabled:opacity-50" options={[...MODES.map(mode => ({ value: mode.value, label: mode.label }))]} readOnly={modeUnavailable} readOnlyReason={modeReason} />
        </Row>
        {state.draft.mode === 'primary' && <Row label="Authority handoff" hint="After Primary mode reaches the safe turn boundary, review the separate writer handoff below.">
          <span data-type="label-s" className="inline-flex items-center gap-xs text-warn"><Clock3 size={14} /> Writer review required</span>
        </Row>}
        {authorityOwnsWrites && <Row label="Change mode" hint="Rollback writer authority to Gideon before moving away from Primary."><span data-type="body-s" className="text-on-surface-low">Locked during Hypermid writer ownership</span></Row>}
      </RowGroup>
    </Section>
    <AuthorityControl onStatus={(next) => setAuthorityOwnsWrites(next.owns_writes)} onCommitted={() => { config.refresh(); setState(undefined) }} />
    <Section title="Strictness" hint="These policies govern overflow and refusal without changing the runtime mode.">
      <RowGroup>
        <Row label="When context overflows">
          <Select value={state.draft.overflow_policy} onChange={(value) => setDraft({ overflow_policy: value as HypermidRuntimeConfigValueWire['overflow_policy'] })} ariaLabel="Context overflow policy" className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" options={[{ value: "reclaim_then_refuse", label: "Reclaim, then refuse" }, { value: "refuse_immediately", label: "Refuse immediately" }]} />
        </Row>
        <Row label="When a projection is refused">
          <Select value={state.draft.refusal_policy} onChange={(value) => setDraft({ refusal_policy: value as HypermidRuntimeConfigValueWire['refusal_policy'] })} ariaLabel="Projection refusal policy" className="min-h-11 rounded-lg border border-outline-variant bg-surface px-m text-on-surface focus:outline-none focus:ring-2 focus:ring-primary" options={[{ value: "refuse", label: "Refuse the turn" }, { value: "compatible_last_known_good", label: "Use compatible last known good" }, { value: "host_passthrough", label: "Use Gideon pass-through" }]} />
        </Row>
      </RowGroup>
    </Section>
    <Section title="Optional work" hint="These capabilities become effective only when the active mode permits them.">
      <RowGroup>
        {([
          ['background_summaries', 'Background summaries'], ['reduction_tools', 'Reduction tools'], ['automatic_reclaim', 'Automatic reclaim'],
          ['nudges', 'Context nudges'], ['subagent_contributions', 'Subagent contributions'], ['synthetic_hook_blocks', 'Synthetic hook blocks'],
        ] as const).map(([key, label]) => <Row key={key} label={label}><Toggle label={label} on={state.draft.features[key]} onChange={(value) => feature(key, value)} /></Row>)}
      </RowGroup>
    </Section>
    {state.conflict && <div role="alert" className="mb-l rounded-lg border border-warn/40 bg-warn/10 p-m">
      <div data-type="body-s" className="flex items-center gap-s text-on-surface"><AlertTriangle size={16} className="text-warn" />{state.conflict.message}</div>
      <div className="mt-m flex flex-wrap justify-end gap-s"><Button size="sm" variant="secondary" onClick={() => setState(reloadRevisionedDraft(state))}>Reload current settings</Button>
        <Button size="sm" onClick={() => setState(reapplyRevisionedDraft(state))}>Reapply my draft</Button></div>
    </div>}
    {config.data.validation.length > 0 && <ul data-type="body-s" role="alert" className="mb-m rounded-lg bg-danger/10 p-m text-danger">{config.data.validation.map((item) => <li key={item}>{item}</li>)}</ul>}
    {error && <p data-type="body-s" role="alert" className="mb-m text-danger">{error}</p>}
    {notice && <p data-type="body-s" role="status" className="mb-m text-success">{notice}</p>}
    <div className="flex flex-wrap justify-end gap-s">
      <Button size="sm" variant="secondary" onClick={() => { config.refresh(); setState(undefined) }}><RefreshCw size={14} /> Reload</Button>
      <Button size="sm" disabled={!dirty || !config.data.editable} disabledReason={!config.data.editable ? 'Runtime policy does not permit editing these settings.' : !dirty ? 'Make a settings change before staging.' : undefined} loading={saving} onClick={() => void save()}>Stage settings</Button>
    </div>
  </>
}
