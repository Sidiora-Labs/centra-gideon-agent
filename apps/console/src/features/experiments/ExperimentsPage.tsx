import { useCallback, useEffect, useState } from 'react'
import { FlaskConical, RefreshCw } from 'lucide-react'
import type { RouteProps } from '../../app/shell/useQueryState'
import { api, type ExperimentCampaign, type ExperimentReplay } from '../../shared/data/api'
import { TopBar } from '../../shared/ui/TopBar'
import { PageTitle } from '../../shared/ui/PageTitle'
import { Button } from '../../shared/ui/Button'
import { QuietButton } from '../../shared/ui/QuietButton'
import { Field, NumberField, TextArea, TextInput } from '../../shared/ui/forms'

export function ExperimentsPage({ navigate, sub }: RouteProps) {
  const [campaigns, setCampaigns] = useState<ExperimentCampaign[]>([])
  const [selected, setSelected] = useState<ExperimentCampaign | null>(null)
  const [definitions, setDefinitions] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState('')
  const [objective, setObjective] = useState('')
  const [workflow, setWorkflow] = useState('')
  const [metric, setMetric] = useState('')
  const [direction, setDirection] = useState<'maximize' | 'minimize'>('maximize')
  const [variantsText, setVariantsText] = useState('[\n  {}\n]')
  const [parallel, setParallel] = useState(1)
  const [maxTokens, setMaxTokens] = useState(0)
  const [recordRun, setRecordRun] = useState('')
  const [replayId, setReplayId] = useState('')
  const [candidateRun, setCandidateRun] = useState('')
  const [replay, setReplay] = useState<ExperimentReplay | null>(null)

  const campaignId = sub?.split('/')[0] || ''
  const refresh = useCallback(async () => {
    try {
      const [list, detail] = await Promise.all([
        api.experimentCampaigns(),
        campaignId ? api.experimentCampaign(campaignId) : Promise.resolve(null),
      ])
      setCampaigns(list.campaigns)
      setSelected(detail)
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load experiments')
    } finally {
      setLoading(false)
    }
  }, [campaignId])

  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void refresh() }, 6000)
    return () => window.clearInterval(timer)
  }, [refresh])
  useEffect(() => { api.workflowDefs().then(({ defs }) => setDefinitions(defs.map(d => d.name))).catch(() => {}) }, [])

  const act = async (fn: () => Promise<ExperimentCampaign>) => {
    setBusy(true)
    try {
      setSelected(await fn())
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The experiment action failed')
    } finally { setBusy(false) }
  }

  const create = async () => {
    let variants: Record<string, unknown>[]
    try {
      const parsed: unknown = JSON.parse(variantsText)
      if (!Array.isArray(parsed) || !parsed.length || parsed.length > 20 || !parsed.every(v => v && typeof v === 'object' && !Array.isArray(v))) {
        throw new Error('Enter 1 to 20 workflow input objects in a JSON array.')
      }
      variants = parsed as Record<string, unknown>[]
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Invalid variants JSON')
      return
    }
    setBusy(true)
    try {
      const result = await api.createExperimentCampaign({ title, objective, workflow_name: workflow, metric, direction, variants, max_parallel: parallel, max_tokens: maxTokens })
      navigate(`experiments/${result.id}`)
      setTitle('')
      await refresh()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not create campaign')
    } finally { setBusy(false) }
  }

  const capture = async () => {
    setBusy(true)
    try {
      const result = await api.recordExperimentRun(recordRun.trim())
      setReplayId(result.id)
      setCandidateRun('')
      setReplay(await api.compareExperimentReplay(result.id))
      setError('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not record run')
    } finally { setBusy(false) }
  }
  const compare = async () => {
    setBusy(true)
    try { setReplay(await api.compareExperimentReplay(replayId.trim(), candidateRun.trim())); setError('') }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not compare recording') }
    finally { setBusy(false) }
  }

  return <div className="flex h-full flex-col">
    <TopBar left={<div className="flex items-center gap-s"><FlaskConical size={18} /><PageTitle>Experiments</PageTitle></div>}
      right={<QuietButton title="Refresh experiments" onClick={() => void refresh()}><RefreshCw size={14} /> Refresh</QuietButton>} />
    <div className="min-h-0 flex-1 overflow-y-auto p-l space-y-l">
      {error && <p role="alert" className="rounded-md bg-danger/10 p-m text-danger">{error}</p>}
      {campaignId && selected ? <section className="space-y-m rounded-xl bg-surface-container p-l">
        <div className="flex flex-wrap items-center justify-between gap-m">
          <div><h2 data-type="title-m">{selected.title}</h2><p data-type="body-s" className="text-on-surface-low">{selected.objective}</p></div>
          <QuietButton onClick={() => navigate('experiments')}>All campaigns</QuietButton>
        </div>
        <p data-type="body-s">Workflow: {selected.workflow_name} · Metric: {selected.metric} ({selected.direction}) · Slots: {selected.max_parallel} · Tokens recorded: {selected.total_tokens ?? 'unknown'}{selected.max_tokens ? ` / ${selected.max_tokens}` : ''}</p>
        <div className="flex flex-wrap gap-s">
          <Button size="sm" loading={busy} disabled={selected.status !== 'active'} onClick={() => void act(() => api.advanceExperimentCampaign(selected.id))}>Launch queued attempts</Button>
          <Button size="sm" variant="secondary" disabled={selected.status !== 'active'} onClick={() => void act(() => api.stopExperimentCampaign(selected.id))}>Stop new launches</Button>
        </div>
        {selected.best_attempt === null ? <p data-type="body-s" className="text-on-surface-low">No valid scored result yet.</p> : <p data-type="body-s">Best valid attempt: #{selected.best_attempt + 1}</p>}
        <div className="space-y-s">{selected.attempts.map(attempt => <AttemptRow key={attempt.ordinal} campaign={selected} ordinal={attempt.ordinal} onObserve={(score, valid, observation) => void act(() => api.observeExperimentAttempt(selected.id, attempt.ordinal, { score, valid, observation }))} navigate={navigate} />)}</div>
      </section> : <>
        <section className="space-y-m rounded-xl bg-surface-container p-l">
          <h2 data-type="title-m">New campaign</h2>
          <p data-type="body-s" className="text-on-surface-low">Compare bounded workflow input variants. Each attempt creates a real workflow run; record a score and observation after it completes.</p>
          <div className="grid gap-m md:grid-cols-2">
            <Field label="Title"><TextInput value={title} onChange={setTitle} /></Field>
            <Field label="Workflow"><select aria-label="Workflow" value={workflow} onChange={e => setWorkflow(e.target.value)} className="h-10 w-full rounded-md bg-surface-high px-m"><option value="">Select a workflow</option>{definitions.map(name => <option key={name} value={name}>{name}</option>)}</select></Field>
            <Field label="Objective"><TextInput value={objective} onChange={setObjective} /></Field>
            <Field label="Metric"><TextInput value={metric} onChange={setMetric} /></Field>
            <Field label="Direction"><select aria-label="Direction" value={direction} onChange={e => setDirection(e.target.value as 'maximize' | 'minimize')} className="h-10 w-full rounded-md bg-surface-high px-m"><option value="maximize">Maximize</option><option value="minimize">Minimize</option></select></Field>
            <div className="flex gap-l"><Field label="Concurrent slots"><NumberField value={parallel} onChange={setParallel} min={1} max={4} /></Field><Field label="Token stop threshold"><NumberField value={maxTokens} onChange={setMaxTokens} min={0} /></Field></div>
          </div>
          <Field label="Workflow input variants" hint="JSON array; one object per attempt, up to 20"><TextArea value={variantsText} onChange={setVariantsText} rows={5} mono /></Field>
          <Button disabled={!title.trim() || !objective.trim() || !workflow || !metric.trim()} loading={busy} onClick={() => void create()}>Create campaign</Button>
        </section>
        <section className="space-y-s"><h2 data-type="title-m">Campaigns</h2>{loading ? <p role="status">Loading campaigns…</p> : campaigns.length ? campaigns.map(item => <button key={item.id} onClick={() => navigate(`experiments/${item.id}`)} className="block w-full rounded-lg bg-surface-container p-m text-left hover:bg-surface-high"><span className="font-medium">{item.title}</span><span className="ml-m text-on-surface-low">{item.status} · {item.attempts.filter(a => a.state === 'complete').length}/{item.attempts.length} complete</span></button>) : <p className="text-on-surface-low">No campaigns yet.</p>}</section>
      </>}
      <section className="space-y-m rounded-xl bg-surface-container p-l">
        <h2 data-type="title-m">Recorded run comparison</h2>
        <p data-type="body-s" className="text-on-surface-low">Freeze a workflow ledger, then compare it with the same run or another run. Inspection reads recorded events and executes no tools.</p>
        <div className="flex flex-wrap items-end gap-s"><Field label="Workflow run ID"><TextInput value={recordRun} onChange={setRecordRun} /></Field><Button size="sm" disabled={!recordRun.trim()} loading={busy} onClick={() => void capture()}>Record run</Button></div>
        <div className="flex flex-wrap items-end gap-s"><Field label="Recording ID"><TextInput value={replayId} onChange={setReplayId} /></Field><Field label="Compare run ID (optional)"><TextInput value={candidateRun} onChange={setCandidateRun} /></Field><Button size="sm" variant="secondary" disabled={!replayId.trim()} loading={busy} onClick={() => void compare()}>Inspect divergence</Button></div>
        {replay && <div aria-live="polite" className="space-y-s"><p>{replay.matches ? 'Recorded events match.' : `${replay.divergence.length}${replay.truncated ? '+' : ''} differences found.`} {replay.recorded_events} recorded events; {replay.candidate_events} candidate events.</p>
          <div className="grid gap-s md:grid-cols-2">{replay.candidate_steps.map((step, index) => <details key={`${step.instance_path}-${step.epoch}-${index}`} className="rounded-md bg-surface-high p-m"><summary className="cursor-pointer">{step.node_id || step.instance_path} · {step.state} · {step.events.length} events</summary><div className="mt-s space-y-xs font-mono text-xs">{step.events.map((event, eventIndex) => <p key={eventIndex} className="break-all">{event.kind ? String(event.kind) : 'event'} · {JSON.stringify(event)}</p>)}</div></details>)}</div>
          {replay.divergence.length > 0 && <div className="max-h-72 overflow-y-auto rounded-md bg-surface-high p-m font-mono text-xs">{replay.divergence.map((item, index) => <p key={index} className="break-all">{item.path}: {String(item.recorded ?? 'absent')} → {String(item.candidate ?? 'absent')}</p>)}</div>}</div>}
      </section>
    </div>
  </div>
}

function AttemptRow({ campaign, ordinal, onObserve, navigate }: {
  campaign: ExperimentCampaign; ordinal: number
  onObserve: (score: number, valid: boolean, observation: string) => void
  navigate: RouteProps['navigate']
}) {
  const attempt = campaign.attempts[ordinal]
  const [score, setScore] = useState(attempt.score ?? 0)
  const [valid, setValid] = useState(attempt.valid ?? true)
  const [observation, setObservation] = useState(attempt.observation)
  return <div className="space-y-s rounded-lg bg-surface-high p-m">
    <div className="flex flex-wrap items-center gap-m"><strong>Attempt #{ordinal + 1}</strong><span>{attempt.state}</span>{attempt.run_id && <button className="text-primary underline" onClick={() => navigate(`workflows/runs/${attempt.run_id}`)}>View run {attempt.run_id}</button>}{attempt.score !== null && <span>Score: {attempt.score} · {attempt.valid ? 'valid' : 'invalid'}</span>}</div>
    <p data-type="caption" className="break-all text-on-surface-low">Inputs: {JSON.stringify(attempt.inputs)}{attempt.tokens != null ? ` · ${attempt.tokens} tokens` : ''}</p>
    {attempt.run_error && <p role="status" className="text-danger">{attempt.run_error}</p>}
    {attempt.state === 'complete' && <div className="grid gap-s md:grid-cols-[auto_auto_1fr_auto] md:items-end"><Field label="Score"><NumberField value={score} onChange={setScore} /></Field><Field label="Validity"><select aria-label={`Attempt ${ordinal + 1} validity`} value={valid ? 'valid' : 'invalid'} onChange={e => setValid(e.target.value === 'valid')} className="h-9 rounded-md bg-surface-container px-s"><option value="valid">Valid</option><option value="invalid">Invalid</option></select></Field><Field label="Observation"><TextInput value={observation} onChange={setObservation} /></Field><Button size="sm" disabled={!observation.trim()} onClick={() => onObserve(score, valid, observation)}>Save observation</Button></div>}
    {attempt.state === 'launching' && <p data-type="caption" className="text-warning">Launch was interrupted before its run ID was recorded. Inspect workflow runs before starting another attempt.</p>}
  </div>
}
