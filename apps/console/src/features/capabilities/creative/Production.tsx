import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Candidate = { chapter_id: string; work_id: string; artifact_id: string; artifact_version: number; characters: number }
type Approval = { kind: string; chapter_id: string; finding_count?: number }
type ChapterState = { chapter_id: string; work_revision?: number }
type Run = { id: string; status: string; mode: string; cursor: number; pause_reason?: string; residual: unknown[]; candidate?: Candidate; approval?: Approval; events: { type: string; at: string }[]; stale?: boolean; chapter_status?: ChapterState[] }
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'

export default function Production({ id, revision, apiRoot }: { id: string; revision: number; apiRoot: string }) {
  const root = `${apiRoot}/${id}/production`
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState<Run | null>(null)
  const [mode, setMode] = useState('authored')
  const [attempts, setAttempts] = useState(2)
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Series production request failed')
  async function load(selectId?: string) {
    const result = await requestJson<{ items: Run[] }>(root)
    setRuns(result.items)
    const target = selectId || selected?.id
    if (target) setSelected(await requestJson<Run>(`${root}/${target}`))
  }
  useEffect(() => { let alive = true; requestJson<{ items: Run[] }>(root).then(value => { if (alive) setRuns(value.items) }).catch(e => { if (alive) fail(e) }); return () => { alive = false } }, [root])
  async function start() {
    setBusy(true); setError(''); setMessage('')
    try { const run = await requestJson<Run>(root, 'POST', { request_id: requestId, series_revision: revision, mode, max_attempts: attempts }); await load(run.id); setMessage('Bounded production run started.'); setRequestId(crypto.randomUUID()) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function action(name: string, body: unknown = {}) {
    if (!selected) return
    setBusy(true); setError(''); setMessage('')
    try { const run = await requestJson<Run>(`${root}/${selected.id}/${name}`, 'POST', body); await load(run.id); setMessage(`Production ${name} recorded.`) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function submit() {
    if (!selected) return
    setBusy(true); setError(''); setMessage('')
    try { const chapter = selected.residual[0] as { chapter_id?: string }; const state = await requestJson<Run>(`${root}/${selected.id}`); const target = state.chapter_status?.[state.cursor] as { work_revision?: number } | undefined
      const run = await requestJson<Run>(`${root}/${selected.id}/submit`, 'POST', { request_id: requestId, work_revision: target?.work_revision, text, note }); await load(run.id); setMessage(`Authored candidate prepared for ${chapter?.chapter_id || 'chapter'}.`); setRequestId(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  return <section aria-label="Series production" className="space-y-3 rounded border border-outline p-3">
    <h2>Bounded series production</h2><p>Each call advances one durable stage. Generated drafts remain immutable candidates until approval; editorial residuals never become success automatically.</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <label className="block">Production mode<select className={control} value={mode} onChange={e => setMode(e.target.value)}><option>authored</option><option>model</option></select></label>
    <label className="block">Maximum model attempts<input className={control} type="number" min={1} max={3} value={attempts} onChange={e => setAttempts(Number(e.target.value))} /></label>
    <Button disabled={busy || attempts < 1 || attempts > 3} onClick={() => void start()}>Start production run</Button>
    <ul>{runs.map(run => <li key={run.id}><Button disabled={busy} onClick={() => void load(run.id)}>{run.id.slice(0, 8)} · {run.status}</Button></li>)}</ul>
    {selected && <section aria-label="Production run"><h3>Run {selected.id.slice(0, 8)}</h3><p>Status {selected.status} · cursor {selected.cursor}{selected.pause_reason && ` · ${selected.pause_reason}`}{selected.stale && ' · source changed'}</p>
      <p>Residual records: {selected.residual.length}</p>{selected.candidate && <p>Candidate {selected.candidate.chapter_id} · {selected.candidate.characters} characters · artifact {selected.candidate.artifact_id}@{selected.candidate.artifact_version}</p>}
      {selected.status === 'running' && <Button disabled={busy} onClick={() => void action('advance')}>Advance one stage</Button>}
      {selected.status === 'running' && <Button disabled={busy} onClick={() => void action('pause')}>Pause after current stage</Button>}
      {!['done', 'canceled', 'exhausted'].includes(selected.status) && <Button disabled={busy} onClick={() => void action('cancel')}>Cancel production</Button>}
      {selected.status === 'paused' && <Button disabled={busy} onClick={() => void action('resume')}>Resume production</Button>}
      {selected.status === 'paused' && selected.pause_reason === 'authored_candidate_required' && <><label className="block">Authored chapter candidate<textarea className={control} value={text} onChange={e => setText(e.target.value)} /></label><label className="block">Candidate note<input className={control} value={note} onChange={e => setNote(e.target.value)} /></label><Button disabled={busy || !text.trim()} onClick={() => void submit()}>Prepare authored candidate</Button></>}
      {selected.status === 'awaiting_approval' && selected.approval?.kind === 'draft' && <Button disabled={busy} onClick={() => void action('approve', { decision: 'apply' })}>Approve candidate draft</Button>}
      {selected.status === 'awaiting_approval' && selected.approval?.kind === 'chapter_review' && <Button disabled={busy} onClick={() => void action('approve', { decision: 'review' })}>Approve chapter review</Button>}
      {selected.status === 'awaiting_approval' && selected.approval?.kind === 'quality' && <><Button disabled={busy} onClick={() => void action('approve', { decision: 'keep' })}>Keep generated draft</Button><Button disabled={busy} onClick={() => void action('rollback')}>Rollback generated draft</Button></>}
      <ol>{selected.events.map((event, index) => <li key={`${event.at}-${index}`}>{event.type} · {event.at}</li>)}</ol>
    </section>}
  </section>
}
