import { useEffect, useRef, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { PinnedTiles } from '../../dashboard/PinnedTiles'
import { Checkbox, Field, NumberField, Select } from '../../../shared/ui/forms'
type Preferences = { revision: number; show_clock: boolean; font_scale: number; idle_seconds: number }
type Card = { state: string; error?: string; title?: string; body?: string; rows?: { id?: string; title?: string; status?: string; enabled?: boolean; next_fire_at?: string; ref?: string; start_at?: string; end_at?: string; kind?: string; observed_at?: string; unit?: string; values?: Record<string, number> }[]; age?: number | null; as_of?: string; tasks_done?: number; planned_completed_minutes?: number; missing_task_ids?: string[]; invalid_task_ids?: string[] }
type Snapshot = { preferences: Preferences; observed_at: string; cards: Record<string, Card> }
export default function AmbientDisplay({ baseUrl = '/api/capabilities/experience', onClose }: { baseUrl?: string; onClose: () => void }) {
  const root = useRef<HTMLElement>(null)
  const chrome = useRef<HTMLDivElement>(null)
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [draft, setDraft] = useState<Preferences | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [idle, setIdle] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const [now, setNow] = useState(new Date())
  const lastInput = useRef(Date.now())
  const active = useRef(true)
  const refresh = async (resetDraft = false) => {
    try {
      const result = await requestJson<Snapshot>(baseUrl + '/ambient')
      if (active.current) { setSnapshot(result); setDraft(previous => resetDraft ? result.preferences : previous || result.preferences); setError('') }
    } catch (cause) { if (active.current) setError(String(cause)) }
  }
  const close = () => {
    if (document.fullscreenElement === root.current) void document.exitFullscreen().catch(cause => setNotice(String(cause)))
    onClose()
  }
  useEffect(() => {
    active.current = true
    void refresh()
    const visible = () => { if (!document.hidden) void refresh() }
    const observe = () => setFullscreen(document.fullscreenElement === root.current)
    const keys = (event: KeyboardEvent) => { lastInput.current = Date.now(); setIdle(false); if (event.key === 'Escape') close() }
    document.addEventListener('visibilitychange', visible)
    document.addEventListener('fullscreenchange', observe)
    window.addEventListener('keydown', keys)
    const timer = setInterval(visible, 5000)
    return () => { active.current = false; clearInterval(timer); document.removeEventListener('visibilitychange', visible); document.removeEventListener('fullscreenchange', observe); window.removeEventListener('keydown', keys) }
  }, [baseUrl])
  useEffect(() => {
    const timer = setInterval(() => {
      setNow(new Date())
      if (snapshot && Date.now() - lastInput.current > snapshot.preferences.idle_seconds * 1000 && !chrome.current?.contains(document.activeElement)) setIdle(true)
    }, 1000)
    return () => clearInterval(timer)
  }, [snapshot?.preferences.idle_seconds])
  const enterFullscreen = async () => {
    if (!root.current?.requestFullscreen) { setNotice('Fullscreen is unavailable in this browser.'); return }
    try { await root.current.requestFullscreen() } catch (cause) { setNotice(`Fullscreen was not entered: ${String(cause)}`) }
  }
  const save = async () => {
    if (!draft) return
    try {
      const result = await requestJson<{ preferences: Preferences }>(baseUrl + '/ambient', 'PUT', draft)
      if (active.current) { setDraft(result.preferences); await refresh() }
    } catch (cause) { if (active.current) setError(String(cause)) }
  }
  const wake = () => { lastInput.current = Date.now(); setIdle(false) }
  return <main ref={root} aria-label="Ambient display" className="fixed inset-0 z-50 overflow-auto bg-canvas text-on-surface p-4 sm:p-8" onPointerMove={wake} onPointerDown={wake} onFocus={wake} style={{ fontSize: `${snapshot?.preferences.font_scale || 1}rem` }}>
    <div ref={chrome} className="sticky top-0 z-10 flex flex-wrap items-end gap-s border-b border-outline-variant/30 bg-surface/95 px-l py-s backdrop-blur" style={idle ? { opacity: 0 } : undefined}>
      <Button onClick={close}>Exit ambient display</Button>
      <Button onClick={() => void enterFullscreen()} disabled={fullscreen}>{fullscreen ? 'Fullscreen active' : 'Enter fullscreen'}</Button>
      <Button onClick={() => void refresh(true)}>Refresh display</Button>
      {draft && <form onSubmit={event => { event.preventDefault(); void save() }} className="sticky top-0 z-10 flex flex-wrap items-end gap-s border-b border-outline-variant/30 bg-surface/95 px-l py-s backdrop-blur">
        <label className="flex items-center gap-xs"><Checkbox ariaLabel="Show clock" checked={draft.show_clock} onChange={show_clock => setDraft({ ...draft, show_clock })} /> Show clock</label>
        <div className="w-24"><Field label="Text size"><Select size="sm" value={String(draft.font_scale)} onChange={value => setDraft({ ...draft, font_scale: Number(value) })} options={[1,2,3].map(value=>({value:String(value),label:String(value)}))} /></Field></div>
        <Field label="Hide controls after seconds"><NumberField value={draft.idle_seconds} min={10} max={300} onChange={idle_seconds => setDraft({ ...draft, idle_seconds })} /></Field>
        <Button type="submit">Save display preferences</Button>
      </form>}
    </div>
    <h1>Ambient display</h1>
    {snapshot?.preferences.show_clock && <time aria-label="Current time" dateTime={now.toISOString()} className="block text-4xl sm:text-6xl my-6">{now.toLocaleTimeString()}</time>}
    {notice && <p role="status">{notice}</p>}
    {error && <p role="alert">Display refresh failed; shown data may be stale. {error}</p>}
    {!snapshot && !error && <p role="status">Loading ambient data…</p>}
    {snapshot && <><p>Last observed: <time dateTime={snapshot.observed_at}>{snapshot.observed_at}</time></p><div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-6">
      {(['schedule', 'work'] as const).map(name => <section key={name} aria-label={name === 'schedule' ? 'Scheduled automations' : 'Native tasks'} className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface p-m">
        <h2 data-type="title-m">{name === 'schedule' ? 'Scheduled automations' : 'Native tasks'}</h2>
        {snapshot.cards[name].state === 'error' ? <p role="alert">{snapshot.cards[name].error}</p> : <>{!snapshot.cards[name].rows?.length && <p>No saved {name === 'schedule' ? 'automations' : 'native tasks'}.</p>}<ul>{snapshot.cards[name].rows?.map(row => <li key={row.id} className="break-words my-2">{row.title} — {name === 'schedule' ? `${row.enabled ? 'Enabled' : 'Disabled'} · ${row.next_fire_at || 'Next run unavailable'}` : row.status}</li>)}</ul></>}
      </section>)}
      <section aria-label="Proactive digest" className="min-w-0 rounded-lg border border-outline-variant/30 bg-surface p-m"><h2 data-type="title-m">Proactive digest</h2>{snapshot.cards.digest.state === 'ready' ? <><h3 data-type="title-m">{snapshot.cards.digest.title}</h3><p className="whitespace-pre-wrap break-words">{snapshot.cards.digest.body}</p></> : <p>{snapshot.cards.digest.error || `Digest ${snapshot.cards.digest.state}`}</p>}</section>
      {['health','goals','calendar'].map(name => <section key={name} aria-label={name} className="rounded-lg border border-outline-variant/30 bg-surface p-m"><h2 data-type="title-m">{name === 'health' ? 'Recorded health measurements' : name === 'goals' ? 'Goals' : 'Planned sessions'}</h2>{snapshot.cards[name].state === 'error' ? <p role="alert">{snapshot.cards[name].error}</p> : <>{!snapshot.cards[name].rows?.length && <p>No recorded {name === 'health' ? 'measurements; health values unknown' : name === 'goals' ? 'goals' : 'sessions'}.</p>}<ul>{snapshot.cards[name].rows?.map(row => <li key={row.id} className="break-words my-2">{name === 'health' ? <>{row.kind}: {Object.entries(row.values || {}).map(([key, value]) => `${key} ${value} ${row.unit}`).join(', ')} · Recorded {row.observed_at}</> : <>{row.title} — {row.status}{row.start_at ? ` · ${row.start_at} to ${row.end_at}` : ''}</>}</li>)}</ul></>}</section>)}
      <section aria-label="Progress" className="rounded-lg border border-outline-variant/30 bg-surface p-m"><h2 data-type="title-m">Recorded progress</h2>{snapshot.cards.progress.state !== 'ready' ? <p role={snapshot.cards.progress.state === 'error' ? 'alert' : undefined}>{snapshot.cards.progress.error}</p> : <><p>Age: {snapshot.cards.progress.age ?? 'Unknown'} · As of {snapshot.cards.progress.as_of}</p><p>Completed tracked tasks: {snapshot.cards.progress.tasks_done} · Planned completed session minutes: {snapshot.cards.progress.planned_completed_minutes}</p>{!!(snapshot.cards.progress.missing_task_ids?.length || snapshot.cards.progress.invalid_task_ids?.length) && <p>Some tracked task sources are missing or invalid.</p>}</>}</section>
      {['external_calendar','mortality'].map(name => <section key={name} aria-label={name} className="rounded-lg border border-outline-variant/30 bg-surface p-m"><p>{snapshot.cards[name].error}</p></section>)}
    </div><section aria-label="Pinned dashboard tiles" className="mt-6">{snapshot.cards.tiles.state === 'error' ? <p role="alert">{snapshot.cards.tiles.error}</p> : snapshot.cards.tiles.rows?.some(row => row.ref?.startsWith('artifact:')) ? <PinnedTiles /> : <p>No pinned artifact tiles.</p>}</section></>}
  </main>
}
