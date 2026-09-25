import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Surface } from '../../../shared/ui/Surface'
import { Field, NumberField, Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { ListScaffold } from '../../../shared/ui/ListScaffold'

type Candidate = { chapter_id: string; work_id: string; artifact_id: string; artifact_version: number; characters: number }
type Approval = { kind: string; chapter_id: string; finding_count?: number }
type ChapterState = { chapter_id: string; work_revision?: number }
type Run = { id: string; status: string; mode: string; cursor: number; pause_reason?: string; residual: unknown[]; candidate?: Candidate; approval?: Approval; events: { type: string; at: string }[]; stale?: boolean; chapter_status?: ChapterState[] }

export default function Production({ id, revision, apiRoot }: { id: string; revision: number; apiRoot: string }) {
  const t = (value: string) => value
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
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Series production request failed'))
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
  return <ListScaffold title={t('Bounded series production')}><section aria-label={t('Series production')} className="space-y-l">
    <p data-type="body-m" className="max-w-[48rem] text-on-surface-low">{t('Each call advances one durable stage. Generated drafts remain immutable candidates until approval; editorial residuals never become success automatically.')}</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{t(message)}</p>}
    <div className="grid min-w-0 gap-l lg:grid-cols-[minmax(16rem,21rem)_minmax(0,1fr)]"><div className="space-y-l"><Surface className="space-y-m p-l"><h2 data-type="title-m">{t('Start a run')}</h2><Field label={t('Production mode')}><Select value={mode} onChange={setMode} options={[{ value: 'authored', label: t('authored') }, { value: 'model', label: t('model') }]} /></Field>
    <Field label={t('Maximum model attempts')}><NumberField value={attempts} min={1} max={3} width="w-full" onChange={setAttempts} /></Field>
    <Button className="w-full" disabled={busy || attempts < 1 || attempts > 3} onClick={() => void start()}>{t('Start production run')}</Button></Surface>
    <Surface className="space-y-s p-l"><h2 data-type="title-m">{t('Production runs')}</h2>{!runs.length && <p data-type="body-m" className="text-on-surface-low">{t('No production runs yet.')}</p>}<ul className="space-y-xs">{runs.map(run => <li key={run.id}><Button variant={selected?.id === run.id ? 'tonal' : 'ghost'} ariaPressed={selected?.id === run.id} className="w-full justify-start" disabled={busy} onClick={() => void load(run.id)}>{run.id.slice(0, 8)} · {t(run.status)}</Button></li>)}</ul></Surface></div>
    {selected ? <Surface className="space-y-m p-l"><section aria-label={t('Production run')} className="space-y-m"><h2 data-type="title-m">{t('Run')} {selected.id.slice(0, 8)}</h2><p data-type="body-m" className="text-on-surface-low">{t('Status')} {t(selected.status)} · {t('cursor')} {selected.cursor}{selected.pause_reason && ` · ${t(selected.pause_reason)}`}{selected.stale && ` · ${t('source changed')}`}</p>
      <p>{t('Residual records')}: {selected.residual.length}</p>{selected.candidate && <p>{t('Candidate')} {selected.candidate.chapter_id} · {selected.candidate.characters} {t('characters')} · {t('artifact')} {selected.candidate.artifact_id}@{selected.candidate.artifact_version}</p>}
      {selected.status === 'running' && <Button disabled={busy} onClick={() => void action('advance')}>{t('Advance one stage')}</Button>}
      {selected.status === 'running' && <Button disabled={busy} onClick={() => void action('pause')}>{t('Pause after current stage')}</Button>}
      {!['done', 'canceled', 'exhausted'].includes(selected.status) && <Button disabled={busy} onClick={() => void action('cancel')}>{t('Cancel production')}</Button>}
      {selected.status === 'paused' && <Button disabled={busy} onClick={() => void action('resume')}>{t('Resume production')}</Button>}
      {selected.status === 'paused' && selected.pause_reason === 'authored_candidate_required' && <><Field label={t('Authored chapter candidate')}><TextArea value={text} onChange={setText} rows={10} /></Field><Field label={t('Candidate note')}><TextInput value={note} onChange={setNote} /></Field><Button disabled={busy || !text.trim()} onClick={() => void submit()}>{t('Prepare authored candidate')}</Button></>}
      {selected.status === 'awaiting_approval' && selected.approval?.kind === 'draft' && <Button disabled={busy} onClick={() => void action('approve', { decision: 'apply' })}>{t('Approve candidate draft')}</Button>}
      {selected.status === 'awaiting_approval' && selected.approval?.kind === 'chapter_review' && <Button disabled={busy} onClick={() => void action('approve', { decision: 'review' })}>{t('Approve chapter review')}</Button>}
      {selected.status === 'awaiting_approval' && selected.approval?.kind === 'quality' && <><Button disabled={busy} onClick={() => void action('approve', { decision: 'keep' })}>{t('Keep generated draft')}</Button><Button disabled={busy} onClick={() => void action('rollback')}>{t('Rollback generated draft')}</Button></>}
      <ol className="mt-l space-y-xs border-t border-outline-variant/30 pt-l">{selected.events.map((event, index) => <li key={`${event.at}-${index}`}>{t(event.type)} · {event.at}</li>)}</ol>
    </section></Surface> : <Surface className="grid min-h-64 place-items-center p-xl text-center text-on-surface-low">{t('Select a production run to inspect its current stage and approvals.')}</Surface>}</div>
  </section></ListScaffold>
}
