import { useEffect, useState, type ReactNode } from 'react'
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
import { PageTitle } from '../../../shared/ui/PageTitle'
import { TopBar } from '../../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../../shared/ui/WorkbenchLayout'
import { Field, Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { AreaNavigation } from '../AreaNavigation'
import { Archive, CalendarDays, Clock3, ContactRound, Download, History, Inbox, MailPlus, MessageCircle, MessagesSquare, Radio, Send, Smartphone, UsersRound, Workflow, X } from 'lucide-react'

type Identity = { kind: string; value: string }
type Person = { id: string; name: string; identities: Identity[]; ring: string; cadence_days: number; notes: string; revision: number }
type Care = { state: string; days_since: number | null }
type Point = { id: string; occurred_at: string; summary: string; direction: string; source: string }
type Detail = { person: Person; touchpoints: Point[]; care: Care; timezone: string }
const base = '/api/capabilities/communications/people'
const empty = { name: '', identities: [] as Identity[], ring: 'tribe', cadence_days: 30, notes: '' }
const selected = () => new URLSearchParams(location.hash.split('?')[1] || '').get('person') || ''
const inputStyle = 'block h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
const currentView = () => new URLSearchParams(location.hash.split('?')[1] || '').get('view') || 'people'
const views = [
  ['people', 'People', UsersRound], ['inbox', 'Inbox', Inbox], ['calendar', 'Calendar', CalendarDays],
  ['beeper', 'Beeper', MessageCircle], ['outbound', 'Outbound email', MailPlus], ['desktop', 'Desktop imports', Smartphone],
  ['imports', 'People imports', Download], ['threads', 'Threads', MessagesSquare], ['teams', 'Teams', ContactRound],
  ['telegram', 'Telegram', Send], ['social', 'Social accounts', Radio], ['x', 'X reading', X],
  ['stacker', 'Stacker News', Workflow], ['signal', 'Signal archive', Archive], ['lifecycle', 'Lifecycle', History], ['timeline', 'Timeline', Clock3],
] as const

export default function Page() {
  const [people, setPeople] = useState<(Person & { care: Care })[]>([])
  const [view, setView] = useState(currentView)
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
  useEffect(() => { const changed = () => { setId(selected()); setView(currentView()) }; addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [])
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
  const chooseView = (next: string) => { const params = new URLSearchParams(location.hash.split('?')[1] || ''); if (next === 'people') params.delete('view'); else params.set('view', next); location.hash = `#/capabilities/communications${params.size ? `?${params}` : ''}`; setView(next) }
  const panels: Record<string, ReactNode> = { inbox: <MirrorPanel />, calendar: <CalendarPanel />, beeper: <BeeperPanel />, outbound: <OutboundEmailPanel />, desktop: <DesktopPanel />, imports: <ImportPanel onImported={() => setVersion(v => v + 1)} />, threads: <ThreadsPanel />, teams: <TeamsPanel />, telegram: <TelegramPanel />, social: <SocialPanel />, x: <XPanel />, stacker: <StackerPanel />, signal: <SignalArchivePanel />, lifecycle: <LifecyclePanel />, timeline: <TimelinePanel /> }
  const peopleView = <div className="space-y-l">
    <div className="flex flex-wrap items-center justify-between gap-s"><div><h2 data-type="title-m">People and relationships</h2><p data-type="body-s" className="text-on-surface-low">Review contact cadence and record durable relationship history.</p></div><div className="flex gap-s"><Button onClick={() => open('')}>New person</Button><Button variant="secondary" onClick={() => setVersion(v => v + 1)} disabled={busy}>Reload</Button></div></div>
    {error && <p role="alert" className="rounded-lg bg-danger-container p-m text-on-danger-container">{error}</p>}
    {loading ? <p role="status" className="text-on-surface-low">Loading people…</p> : <div className="grid min-w-0 gap-l lg:grid-cols-[minmax(16rem,22rem)_minmax(0,1fr)]">
      <section aria-label="Care list" className="min-w-0 rounded-lg bg-surface-container px-l py-l"><h2 data-type="title-s">Care list</h2>{people.length === 0 && <p className="py-xl text-on-surface-low">No people yet. Add someone to begin tracking contact.</p>}
        <ul className="mt-s space-y-xs">{people.map(person => <li key={person.id}><a href={`#/capabilities/communications?person=${encodeURIComponent(person.id)}`} onClick={() => setId(person.id)} className={`flex min-h-11 items-center justify-between gap-s rounded-lg px-m py-s text-on-surface hover:bg-surface-high ${id === person.id ? 'bg-secondary-container' : ''}`}><span className="truncate font-medium">{person.name}</span><span data-type="caption" className="shrink-0 text-on-surface-low">{person.care.state}{person.care.days_since !== null ? ` · ${person.care.days_since}d` : ''}</span></a></li>)}</ul>
      </section>
      <section aria-label="Person detail" className="min-w-0 space-y-m rounded-lg bg-surface-container px-l py-l">
        <h2 data-type="title-s">{id ? 'Edit person' : 'Add person'}</h2>
        <Field label="Name"><TextInput value={form.name} maxLength={200} onChange={name => setForm({ ...form, name })} /></Field>
        <Field label="Ring"><Select value={form.ring} onChange={ring => setForm({ ...form, ring })} options={['support', 'core', 'tribe', 'village', 'external'].map(value => ({ value, label: value }))} /></Field>
        <label className="block" data-type="label-s">Cadence in days<input type="number" min={1} max={3650} className={inputStyle} value={form.cadence_days} onChange={event => setForm({ ...form, cadence_days: Number(event.target.value) })} /></label>
        <Field label="Identities (one email:, phone:, or handle: per line)"><TextArea value={identities} onChange={setIdentities} rows={4} /></Field>
        <Field label="Notes"><TextArea value={form.notes} onChange={notes => setForm({ ...form, notes: notes.slice(0, 10000) })} rows={5} /></Field>
        <Button onClick={save} disabled={busy || !form.name.trim() || (!!id && !detail)}>Save person</Button>
        {detail && <section className="space-y-m border-t border-outline-variant/20 pt-l"><div><h3 data-type="title-s">Contact history</h3><p data-type="body-s" className="text-on-surface-low">{detail.timezone}</p><p>Care: {detail.care.state}</p></div>
          <Field label="Occurred at (ISO with timezone)"><TextInput value={occurredAt} onChange={setOccurredAt} /></Field>
          <Field label="Direction"><Select value={direction} onChange={setDirection} options={['mutual', 'inbound', 'outbound'].map(value => ({ value, label: value }))} /></Field>
          <Field label="Contact summary"><TextArea value={summary} onChange={value => setSummary(value.slice(0, 2000))} rows={4} /></Field>
          <Button onClick={record} disabled={busy}>Record contact</Button>
          <ul className="space-y-s">{detail.touchpoints.map(point => <li key={point.id} className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m"><time data-type="body-s" className="text-on-surface-low">{point.occurred_at}</time><span data-type="body-s" className="text-on-surface-low"> · {point.direction} · {point.source}</span><p>{point.summary}</p></li>)}</ul>{!detail.touchpoints.length && <p className="text-on-surface-low">No contact recorded.</p>}
        </section>}
      </section>
    </div>}
  </div>
  const destinations = views.map(([viewId, label, icon]) => ({ id: viewId, label, icon }))
  const activeLabel = destinations.find(destination => destination.id === view)?.label || 'Communications'
  return <AreaNavigation label="Communications workspace" items={destinations} active={view} onChange={chooseView}>
    <WorkbenchLayout topBar={<TopBar keepCornerPadding left={<PageTitle>{activeLabel}</PageTitle>} />}>
      <main className="mx-auto min-w-0 space-y-l px-l py-2xl text-on-surface" style={{ maxWidth: 'var(--content-width)' }} aria-label="People and relationships">
        {view === 'people' ? peopleView : panels[view] || peopleView}
      </main>
    </WorkbenchLayout>
  </AreaNavigation>
}
