import { useEffect, useState } from 'react'
import { CheckCircle2, Gauge, RefreshCw, Server, TriangleAlert } from 'lucide-react'
import { api, ApiError, type HypermidModelBindingPlanWire, type HypermidModelBindingsWire, type HypermidModelWire } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { FormSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { Surface } from '../../shared/ui/Surface'
import { StatusPill } from '../settings/bento'
import { Row, RowGroup, Section } from '../settings/settingsUI'
import { humanUnknown, modelIsReady, openRevisionedDraft, reapplyRevisionedDraft, rejectRevisionedDraft, reloadRevisionedDraft, type RevisionedDraft } from './configState'

function ModelFacts({ model }: { model: HypermidModelWire }) {
  return <dl className="mt-s grid gap-xs text-xs text-on-surface-low sm:grid-cols-2">
    <div><dt className="inline">Provider: </dt><dd className="inline text-on-surface">{model.provider_id}</dd></div>
    <div><dt className="inline">Context: </dt><dd className="inline text-on-surface">{humanUnknown(model.context_tokens, (value) => `${Number(value).toLocaleString()} tokens`)}</dd></div>
    <div><dt className="inline">Embedding: </dt><dd className="inline text-on-surface">{humanUnknown(model.embedding_dimensions, (value) => `${value} dimensions`)}</dd></div>
    <div><dt className="inline">Residency: </dt><dd className="inline text-on-surface">{humanUnknown(model.resident, (value) => value ? 'Local' : 'Remote')}</dd></div>
    <div><dt className="inline">Price: </dt><dd className="inline text-on-surface">{humanUnknown(model.price)}</dd></div>
    <div><dt className="inline">Usage: </dt><dd className="inline text-on-surface">{humanUnknown(model.usage)}</dd></div>
  </dl>
}

export function ModelBindings() {
  const bindings = useQuery('hypermid:config:models', () => api.hypermidModelBindings())
  const [state, setState] = useState<RevisionedDraft<Record<string, string>>>()
  const [plan, setPlan] = useState<HypermidModelBindingPlanWire>()
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    if (bindings.data && !state) setState(openRevisionedDraft(bindings.data.bindings, bindings.data.revision_digest))
  }, [bindings.data, state])
  if (!bindings.data && bindings.error) return <LoadError what="Hypermid model bindings" error={bindings.error} onRetry={bindings.refresh} />
  if (!bindings.data || !state) return <FormSkeleton sections={2} rows={3} what="Hypermid model bindings" />
  const data: HypermidModelBindingsWire = bindings.data
  const choose = async (duty: string, modelId: string) => {
    setState((current) => current && ({ ...current, draft: { ...current.draft, [duty]: modelId } }))
    setPlan(undefined); setError(''); setBusy(`plan:${duty}`)
    try { setPlan(await api.planHypermidModelBinding(duty, modelId, state.revision)) }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The binding preview was refused.') }
    finally { setBusy('') }
  }
  const probe = async (model: HypermidModelWire) => {
    setBusy(`probe:${model.id}`); setError('')
    try { await api.probeHypermidModel(model.id); bindings.refresh() }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'The required model probe failed.') }
    finally { setBusy('') }
  }
  const save = async () => {
    if (!plan) return
    setBusy(`save:${plan.duty}`); setError('')
    try {
      const current = await api.saveHypermidModelBinding(plan.duty, plan.model_id, state.revision)
      setState(openRevisionedDraft(current.bindings, current.revision_digest))
      setPlan(undefined); bindings.refresh()
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        try {
          const current = await api.hypermidModelBindings()
          setState((previous) => previous && rejectRevisionedDraft(previous, current.bindings, current.revision_digest,
            'Model bindings changed after you opened them. Your selection is preserved.'))
        } catch { setError(caught.message) }
      } else setError(caught instanceof Error ? caught.message : 'The binding change was refused.')
    } finally { setBusy('') }
  }
  return <Section title="Models" hint="Bindings become ready only after the required real probe succeeds. Unknown metadata stays unknown."
    right={<Button size="sm" variant="secondary" onClick={bindings.refresh}><RefreshCw size={14} /> Refresh</Button>}>
    <RowGroup>{data.duties.map((duty) => {
      const selectedId = state.draft[duty.id] || ''
      const selected = data.models.find((model) => model.id === selectedId)
      const ready = selected ? modelIsReady(selected) : false
      return <Row key={duty.id} label={duty.label} hint={`Required check: ${duty.required_probe}`}>
        <div className="flex flex-wrap items-center justify-end gap-s">
          <select value={selectedId} onChange={(event) => void choose(duty.id, event.target.value)} aria-label={`${duty.label} model`}
            className="min-h-11 max-w-64 rounded-lg border border-outline-variant bg-surface px-m text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary">
            <option value="">Choose a model</option>{data.models.map((model) => <option key={model.id} value={model.id}>{model.display_name || model.id} · {model.provider_id}</option>)}
          </select>
          {selected && <StatusPill label={ready ? 'probe ready' : selected.health === 'unknown' ? 'readiness unknown' : selected.health} tone={ready ? 'ok' : 'warn'} />}
          {selected && !ready && <Button size="sm" variant="secondary" loading={busy === `probe:${selected.id}`} onClick={() => void probe(selected)}><Gauge size={14} /> Run required probe</Button>}
        </div>
      </Row>
    })}</RowGroup>
    {state.draft && <div className="mt-m grid gap-m sm:grid-cols-2">{Object.entries(state.draft).map(([duty, modelId]) => {
      const model = data.models.find((item) => item.id === modelId)
      return model ? <Surface key={duty} tone="container" radius="lg" className="p-m">
        <div className="flex items-center gap-s"><Server size={15} className="text-primary" /><span className="text-sm text-on-surface">{model.display_name || model.id}</span>
          <StatusPill label={modelIsReady(model) ? 'ready' : 'not ready'} tone={modelIsReady(model) ? 'ok' : 'warn'} /></div><ModelFacts model={model} />
      </Surface> : null
    })}</div>}
    {plan && <div className="mt-m rounded-lg border border-outline-variant bg-surface p-m">
      <h3 className="flex items-center gap-s text-sm text-on-surface">{plan.readiness_required ? <TriangleAlert size={15} className="text-warn" /> : <CheckCircle2 size={15} className="text-success" />} Review binding change</h3>
      <p className="mt-xs text-sm text-on-surface-low">Affected duties: {plan.affected_duties.join(', ') || 'Only the selected duty'}.</p>
      <div className="mt-m flex justify-end"><Button size="sm" disabled={plan.readiness_required && !modelIsReady(data.models.find((model) => model.id === plan.model_id) || { availability: '', health: '' })}
        loading={busy === `save:${plan.duty}`} onClick={() => void save()}>Apply binding</Button></div>
    </div>}
    {state.conflict && <div role="alert" className="mt-m rounded-lg border border-warn/40 bg-warn/10 p-m">
      <p className="text-sm text-on-surface">{state.conflict.message}</p><div className="mt-m flex flex-wrap justify-end gap-s">
        <Button size="sm" variant="secondary" onClick={() => setState(reloadRevisionedDraft(state))}>Reload bindings</Button>
        <Button size="sm" onClick={() => setState(reapplyRevisionedDraft(state))}>Reapply selection</Button>
      </div>
    </div>}
    {error && <p role="alert" className="mt-m text-sm text-danger">{error}</p>}
  </Section>
}
