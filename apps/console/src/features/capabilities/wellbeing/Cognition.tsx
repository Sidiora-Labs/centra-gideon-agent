import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'

type Stimulus = { left?: number; right?: number; operator?: string; word?: string; color?: string }
type Trial = { index: number; stimulus: Stimulus; presented_at: string; answer?: string; answered_at?: string; correct?: boolean; elapsed_ms?: number }
type Session = { id: string; kind: string; planned_trials: number; started_at: string; deadline: string; status: string; revision: number; current_trial: Trial | null; trials: Trial[]; score: { answered: number; correct: number; accuracy: number | null; mean_elapsed_ms: number | null; total_elapsed_ms: number } }
const base = '/api/capabilities/wellbeing/cognition/sessions'
const colors: Record<string, string> = { red: '#dc2626', blue: '#2563eb', green: '#16a34a', yellow: '#a16207' }

export default function Cognition() {
  const { query, setQuery } = useHashRoute('capabilities')
  const identity = query.session
  const [sessions, setSessions] = useState<Session[]>([]), [selected, setSelected] = useState<Session | null>(null)
  const [kind, setKind] = useState('arithmetic'), [trials, setTrials] = useState('5'), [limit, setLimit] = useState('60'), [answer, setAnswer] = useState('')
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [generation, setGeneration] = useState(0)
  const receipt = useRef({ fingerprint: '', id: '' })
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setSelected(null); setAnswer('')
    Promise.all([requestJson<{ sessions: Session[] }>(base), identity ? requestJson<Session>(`${base}/${identity}`) : Promise.resolve(null)])
      .then(([catalog, row]) => { if (active) { setSessions(catalog.sessions); setSelected(row) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [identity, generation])
  useEffect(() => {
    if (!selected || selected.status !== 'active') return
    const timer = setTimeout(() => setGeneration(value => value + 1), Math.max(250, new Date(selected.deadline).getTime() - Date.now() + 100))
    return () => clearTimeout(timer)
  }, [selected])
  async function write(operation: 'start' | 'answer' | 'cancel', response?: string) {
    if (busy) return
    const payload = operation === 'start' ? { kind, planned_trials: Number(trials), time_limit_seconds: Number(limit) } : { revision: selected!.revision, ...(operation === 'answer' ? { answer: response ?? answer } : {}) }
    const fingerprint = JSON.stringify([operation, operation === 'start' ? null : identity, payload])
    if (receipt.current.fingerprint !== fingerprint) receipt.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try {
      const result = await requestJson<Session>(operation === 'start' ? base : `${base}/${identity}/${operation === 'answer' ? 'answers' : 'cancel'}`, 'POST', { ...payload, request_id: receipt.current.id })
      setQuery({ session: result.id }); setAnswer(''); setGeneration(value => value + 1)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  function submit(event: FormEvent) { event.preventDefault(); void write('answer') }
  const trial = selected?.current_trial
  return <main className="h-full overflow-auto p-4 sm:p-6 space-y-6 text-on-surface"><h1 data-type="headline-s">Timed cognitive practice</h1><p>Server-observed response times include network latency. Scores describe this practice session and provide no clinical interpretation.</p><Button onClick={() => { receipt.current = { fingerprint: '', id: '' }; setQuery({ session: null }) }}>New exercise</Button>{error && <p role="alert">{error} <Button onClick={() => setGeneration(value => value + 1)}>Reload exercise</Button></p>}{loading && <p role="status">Loading exercise…</p>}
    {!identity && <form onSubmit={e => { e.preventDefault(); void write('start') }} className="space-y-3"><h2 data-type="title-m">Start a session</h2><label className="block">Exercise<select aria-label="Exercise kind" value={kind} onChange={e => setKind(e.target.value)}><option value="arithmetic">Arithmetic</option><option value="color_word">Color word</option></select></label><Field label="Number of trials (1–20)"><TextInput value={trials} onChange={setTrials} /></Field><Field label="Time limit in seconds (1–600)"><TextInput value={limit} onChange={setLimit} /></Field><Button type="submit" loading={busy}>Start exercise</Button></form>}
    {selected && <section className="space-y-4"><h2 data-type="title-m">Session: {selected.status}</h2><p>{selected.kind} · {selected.planned_trials} planned trials · Deadline {selected.deadline}</p>{trial && <section className="space-y-3" aria-label="Current trial"><h3 data-type="title-m">Trial {trial.index}</h3>{selected.kind === 'arithmetic' ? <form onSubmit={submit} className="space-y-3"><p aria-label="Arithmetic question" data-left={trial.stimulus.left} data-right={trial.stimulus.right} data-operator={trial.stimulus.operator}>{trial.stimulus.left} {trial.stimulus.operator} {trial.stimulus.right} = ?</p><Field label="Your arithmetic answer"><TextInput value={answer} onChange={setAnswer} required /></Field><Button type="submit" loading={busy}>Submit answer</Button></form> : <><p>Select the ink color, ignoring the written word.</p><p aria-label="Color word stimulus" data-color={trial.stimulus.color} style={{ color: colors[trial.stimulus.color!] }} className="text-4xl font-bold">{trial.stimulus.word}</p><div className="flex gap-2 flex-wrap">{Object.keys(colors).map(color => <Button key={color} disabled={busy} onClick={() => write('answer', color)}>{color}</Button>)}</div></>}</section>}{selected.status === 'active' && <Button variant="danger" loading={busy} onClick={() => write('cancel')}>Cancel exercise</Button>}<section aria-label="Session score"><p>Answered: {selected.score.answered} · Correct: {selected.score.correct}</p><p>Accuracy: {selected.score.accuracy === null ? 'Not recorded' : `${Math.round(selected.score.accuracy * 100)}%`}</p><p>Mean response time: {selected.score.mean_elapsed_ms === null ? 'Not recorded' : `${Math.round(selected.score.mean_elapsed_ms)} ms`}</p><p>Total recorded response time: {Math.round(selected.score.total_elapsed_ms)} ms</p></section><section aria-label="Recorded trials">{selected.trials.map(row => <p key={row.index}>Trial {row.index}: {row.answer} · {row.correct ? 'correct' : 'incorrect'} · {Math.round(row.elapsed_ms!)} ms</p>)}</section></section>}
    <section className="space-y-2"><h2 data-type="title-m">Practice history</h2>{sessions.map(row => <button key={row.id} className="block rounded-lg bg-surface-container p-3 text-left" onClick={() => setQuery({ session: row.id })}>{row.kind} · {row.status} · {row.started_at}</button>)}{!loading && !sessions.length && <p>No exercise sessions.</p>}</section>
  </main>
}
