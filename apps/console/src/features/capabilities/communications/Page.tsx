import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { TimelinePanel } from './TimelinePanel'
import { TeamsPanel } from './TeamsPanel'
import { SignalArchivePanel } from './SignalArchivePanel'
import { LifecyclePanel } from './LifecyclePanel'
import { StackerPanel } from './StackerPanel'
import { XPanel } from './XPanel'
import { SocialPanel } from './SocialPanel'
import { CalendarPanel } from './CalendarPanel'
import { TelegramPanel } from './TelegramPanel'
import { BeeperPanel } from './BeeperPanel'
import { DesktopPanel } from './DesktopPanel'
import { MirrorPanel } from './MirrorPanel'
import { OutboundEmailPanel } from './OutboundEmailPanel'
import { ThreadsPanel } from './ThreadsPanel'
import { ImportPanel } from './ImportPanel'
import { Button } from '../../../shared/ui/Button'

type Identity = { kind: string; value: string }
type Person = { id: string; name: string; identities: Identity[]; ring: string; cadence_days: number; notes: string; revision: number }
type Care = { state: string; days_since: number | null }
type Point = { id: string; occurred_at: string; summary: string; direction: string; source: string }
type Detail = { person: Person; touchpoints: Point[]; care: Care; timezone: string }
const base = '/api/capabilities/communications/people'
const empty = { name: '', identities: [] as Identity[], ring: 'tribe', cadence_days: 30, notes: '' }
const selected = () => new URLSearchParams(location.hash.split('?')[1] || '').get('person') || ''
const inputStyle = 'block w-full rounded border border-outline bg-surface p-2 text-on-surface'

export default function Page() {
  const [people, setPeople] = useState<(Person & { care: Care })[]>([])
  const [id, setId] = useState(selected)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [form, setForm] = useState({ ...empty })
  const [identities, setIdentities] = useState('')
  const [summary, setSummary] = useState('')
  const [direction, setDirection] = useState('mutual')
  const [occurredAt, setOccurredAt] = useState(new Date().toISOString())
  const [attempt, setAttempt] = useState(crypto.randomUUID())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [version, setVersion] = useState(0)
  useEffect(() => { const changed = () => setId(selected()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [])
  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setDetail(null)
    Promise.all([requestJson<{ people: (Person & { care: Care })[] }>(base), id ? requestJson<Detail>(`${base}/${encodeURIComponent(id)}`) : Promise.resolve(null)])
      .then(([list, item]) => { if (!active) return; setPeople(list.people); setDetail(item); setForm(item?.person || { ...empty }); setIdentities(item?.person.identities.map(i => `${i.kind}:${i.value}`).join('\n') || '') })
      .catch(e => { if (active) setError(String(e.message || e)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [id, version])
  function open(personId: string) { location.hash = `#/capabilities/communications${personId ? `?person=${encodeURIComponent(personId)}` : ''}`; setId(personId) }
  async function save() {
    setBusy(true); setError('')
    try {
      const parsed = identities.split('\n').filter(v => v.trim()).map(line => { const colon = line.indexOf(':'); return { kind: line.slice(0, colon).trim(), value: line.slice(colon + 1).trim() } })
      const body = { name: form.name, ring: form.ring, cadence_days: form.cadence_days, notes: form.notes, identities: parsed, ...(id ? { revision: detail?.person.revision } : {}) }
      const result = await requestJson<{ person: Person }>(id ? `${base}/${encodeURIComponent(id)}` : base, id ? 'PUT' : 'POST', body)
      open(result.person.id); setVersion(v => v + 1)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  async function record() {
    setBusy(true); setError('')
    try {
      await requestJson(`${base}/${encodeURIComponent(id)}/touchpoints`, 'POST', { source: 'manual', external_id: attempt, occurred_at: occurredAt, direction, summary })
      setAttempt(crypto.randomUUID()); setSummary(''); setVersion(v => v + 1)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  return <main className="h-full overflow-auto p-4 text-on-surface" aria-label="People and relationships">
    <h1 className="text-xl">People and relationships</h1>
    <ImportPanel onImported={() => setVersion(v => v + 1)} />
    <TimelinePanel /><LifecyclePanel /><StackerPanel /><XPanel /><SocialPanel /><CalendarPanel /><TelegramPanel /><BeeperPanel /><DesktopPanel /><SignalArchivePanel /><TeamsPanel /><MirrorPanel /><OutboundEmailPanel /><ThreadsPanel />
    <div className="my-3 flex gap-2"><Button onClick={() => open('')}>New person</Button><Button variant="secondary" onClick={() => setVersion(v => v + 1)} disabled={busy}>Reload</Button></div>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {loading ? <p role="status">Loading people…</p> : <div className="grid gap-6 md:grid-cols-2">
      <section aria-label="Care list"><h2>Care list</h2>{people.length === 0 && <p>No people yet. Add someone to begin tracking contact.</p>}
        <ul>{people.map(p => <li key={p.id} className="my-3"><a href={`#/capabilities/communications?person=${encodeURIComponent(p.id)}`} onClick={() => setId(p.id)} className="text-primary underline">{p.name}</a><span> · {p.care.state}{p.care.days_since !== null ? ` · ${p.care.days_since} days since contact` : ''}</span></li>)}</ul>
      </section>
      <section aria-label="Person detail" className="space-y-3">
        <h2>{id ? 'Edit person' : 'Add person'}</h2>
        <label className="block">Name<input className={inputStyle} value={form.name} maxLength={200} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
        <label className="block">Ring<select className={inputStyle} value={form.ring} onChange={e => setForm({ ...form, ring: e.target.value })}>{['support', 'core', 'tribe', 'village', 'external'].map(r => <option key={r}>{r}</option>)}</select></label>
        <label className="block">Cadence in days<input type="number" min={1} max={3650} className={inputStyle} value={form.cadence_days} onChange={e => setForm({ ...form, cadence_days: Number(e.target.value) })} /></label>
        <label className="block">Identities (one email:, phone:, or handle: per line)<textarea className={inputStyle} value={identities} onChange={e => setIdentities(e.target.value)} /></label>
        <label className="block">Notes<textarea className={inputStyle} value={form.notes} maxLength={10000} onChange={e => setForm({ ...form, notes: e.target.value })} /></label>
        <Button onClick={save} disabled={busy || !form.name.trim() || (!!id && !detail)}>Save person</Button>
        {detail && <><h3>Contact history · {detail.timezone}</h3><p>Care: {detail.care.state}</p>
          <label className="block">Occurred at (ISO with timezone)<input className={inputStyle} value={occurredAt} onChange={e => setOccurredAt(e.target.value)} /></label>
          <label className="block">Direction<select className={inputStyle} value={direction} onChange={e => setDirection(e.target.value)}>{['mutual', 'inbound', 'outbound'].map(d => <option key={d}>{d}</option>)}</select></label>
          <label className="block">Contact summary<textarea className={inputStyle} value={summary} maxLength={2000} onChange={e => setSummary(e.target.value)} /></label>
          <Button onClick={record} disabled={busy}>Record contact</Button>
          <ul>{detail.touchpoints.map(t => <li key={t.id} className="my-2"><time>{t.occurred_at}</time> · {t.direction} · {t.source}<p>{t.summary}</p></li>)}</ul>{!detail.touchpoints.length && <p>No contact recorded.</p>}
        </>}
      </section>
    </div>}
  </main>
}
