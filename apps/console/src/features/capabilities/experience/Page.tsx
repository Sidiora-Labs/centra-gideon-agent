import { useEffect, useRef, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import './experience.css'
import Narration from './Narration'
import AmbientDisplay from './AmbientDisplay'
import AvatarPanel from './AvatarPanel'
import NativeCalls from './NativeCalls'
import NativeDuplex from './NativeDuplex'
import WorldEngine from './WorldEngine'
import WorldFoundations from './WorldFoundations'
import GameAssets from './GameAssets'
import Worlds from './Worlds'
import Moltworld from './Moltworld'
import Moltbook from './Moltbook'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { TopBar } from '../../../shared/ui/TopBar'
import { PageTitle } from '../../../shared/ui/PageTitle'
import { HeaderActions } from '../../../shared/ui/HeaderActions'
import { Field, Select, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
import { AreaNavigation } from '../AreaNavigation'
import { BookOpen, Boxes, Gamepad2, Globe2, Mic2, Phone, Radio, Sparkles } from 'lucide-react'

type Choice = { id: string; label: string; target: string }
type Node = { id: string; text: string; kind: 'scene' | 'ending'; choices: Choice[] }
type Story = { id: string; title: string; start_node: string; nodes: Node[]; revision: number }
type Session = { id: string; story_id: string; story_revision: number; current_node: string; revision: number; history: unknown[] }
type View = { session: Session; story: Story; node: Node }
const blank = (): Story => ({ id: '', title: '', start_node: 'opening', revision: 1, nodes: [{ id: 'opening', text: '', kind: 'ending', choices: [] }] })
const token = () => crypto.randomUUID().replaceAll('-', '')
const selected = () => new URLSearchParams(location.hash.split('?')[1] || '')

export default function Page({ baseUrl = '/api/capabilities/experience' }: { baseUrl?: string }) {
  const [stories, setStories] = useState<Story[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [draft, setDraft] = useState<Story>(blank)
  const [view, setView] = useState<View | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [route, setRoute] = useState(location.hash)
  const activeView = new URLSearchParams(route.split('?')[1] || '').get('view') || 'stories'
  const destinations = [
    { id: 'stories', label: 'Interactive stories', icon: BookOpen, group: 'Create' },
    { id: 'games', label: 'Game assets', icon: Gamepad2, group: 'Create' },
    { id: 'world', label: 'World workspace', icon: Globe2, group: 'Worlds' },
    { id: 'foundations', label: 'Foundations', icon: Boxes, group: 'Worlds' },
    { id: 'voice', label: 'Voice and avatar', icon: Mic2, group: 'Presence' },
    { id: 'calls', label: 'Native calls', icon: Phone, group: 'Presence' },
    { id: 'moltworld', label: 'Moltworld', icon: Sparkles, group: 'Connected worlds' },
    { id: 'moltbook', label: 'Moltbook', icon: Radio, group: 'Connected worlds' },
  ] as const
  const selectView = (next: string) => { const query = selected(); if (next === 'stories') query.delete('view'); else query.set('view', next); location.hash = '/capabilities/experience' + (query.size ? '?' + query : '') }
  const pending = useRef<{ key: string; id: string } | null>(null)
  const requestId = (key: string) => {
    if (pending.current?.key !== key) pending.current = { key, id: token() }
    return pending.current.id
  }
  useEffect(() => {
    const change = () => setRoute(location.hash)
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([requestJson<{ stories: Story[] }>(`${baseUrl}/stories`), requestJson<{ sessions: Session[] }>(`${baseUrl}/sessions`)]).then(async ([a, b]) => {
      const query = selected()
      const story = query.get('story'), session = query.get('session')
      const detail = session ? await requestJson<View>(`${baseUrl}/sessions/${encodeURIComponent(session)}`) : null
      const editing = story ? await requestJson<{ story: Story }>(`${baseUrl}/stories/${encodeURIComponent(story)}`) : null
      if (active) { setStories(a.stories); setSessions(b.sessions); setView(detail); setDraft(editing?.story || blank()); setError('') }
    }).catch(cause => { if (active) setError(String(cause)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [baseUrl, route])
  const navigate = (kind: string, id: string) => { const query = selected(); query.delete('story'); query.delete('session'); query.set(kind, id); location.hash = '/capabilities/experience?' + query }
  const run = async (action: () => Promise<void>) => {
    if (busy) return
    setBusy(true); setError('')
    try { await action(); pending.current = null } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  const editNode = (id: string, patch: Partial<Node>) => setDraft(d => ({ ...d, nodes: d.nodes.map(n => n.id === id ? { ...n, ...patch } : n) }))
  const save = () => run(async () => {
    const body = { title: draft.title, start_node: draft.start_node, nodes: draft.nodes, ...(draft.id ? { revision: draft.revision } : {}) }
    const result = await requestJson<{ story: Story }>(`${baseUrl}/stories${draft.id ? `/${draft.id}` : ''}`, draft.id ? 'PUT' : 'POST', body)
    setDraft(result.story); setStories(s => [result.story, ...s.filter(x => x.id !== result.story.id)]); navigate('story', result.story.id)
  })
  const start = (story: Story) => run(async () => {
    const result = await requestJson<View>(`${baseUrl}/sessions`, 'POST', { story_id: story.id, story_revision: story.revision, request_id: requestId(`start:${story.id}:${story.revision}`) })
    setView(result); navigate('session', result.session.id)
  })
  const choose = (choice: Choice) => run(async () => {
    if (!view) return
    const result = await requestJson<View>(`${baseUrl}/sessions/${view.session.id}/choices`, 'POST', { choice_id: choice.id, revision: view.session.revision, request_id: requestId(`${view.session.id}:${view.session.revision}:${choice.id}`) })
    setView(result)
  })
  if (new URLSearchParams(route.split('?')[1] || '').get('ambient') === '1') return <AmbientDisplay baseUrl={baseUrl} onClose={() => { location.hash = '/capabilities/experience' }} />
  return <AreaNavigation label="Experience workspace" items={destinations} active={activeView} onChange={selectView}><div className="flex h-full min-h-0 flex-col text-on-surface">
    <TopBar left={<PageTitle>{destinations.find(item => item.id === activeView)?.label || 'Experience'}</PageTitle>} right={activeView === 'stories' ? <HeaderActions><Button variant="secondary" onClick={() => { const query = selected(); query.set('ambient', '1'); location.hash = '/capabilities/experience?' + query }}>Open ambient display</Button><Button disabled={busy} onClick={() => { location.hash = '/capabilities/experience'; setDraft(blank()); setView(null) }}>New story</Button></HeaderActions> : undefined} />
    <main className="experience-page min-h-0 flex-1 overflow-y-auto" aria-label="Interactive stories"><div className="mx-auto flex w-full flex-col gap-l px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
    {activeView === 'world' && <><WorldEngine baseUrl={baseUrl} /><Worlds baseUrl={baseUrl} /></>}
    {activeView === 'foundations' && <WorldFoundations baseUrl={baseUrl} />}
    {activeView === 'games' && <GameAssets baseUrl={baseUrl} />}
    {activeView === 'voice' && <AvatarPanel baseUrl={baseUrl} />}
    {activeView === 'calls' && <><NativeCalls baseUrl={baseUrl} /><NativeDuplex baseUrl={baseUrl} /></>}
    {activeView === 'moltworld' && <Moltworld baseUrl={baseUrl} />}
    {activeView === 'moltbook' && <Moltbook apiRoot={`${baseUrl}/moltbook`} />}
    {activeView === 'stories' && <>
    <p>Author connected scenes and play choices into different endings.</p>
    {loading && <p role="status">Loading stories…</p>}
    {error && <p role="alert">{error}</p>}
    <nav aria-label="Story library" className="flex flex-wrap gap-s">
      {stories.map(s => <span key={s.id}><Button disabled={busy} onClick={() => navigate('story', s.id)}>{s.title}</Button> <Button disabled={busy} onClick={() => start(s)}>Play {s.title}</Button></span>)}
    </nav>
    {!loading && stories.length === 0 && <p>No stories yet. Write an opening below.</p>}
    {view ? <section aria-label="Story player"><Surface className="space-y-m p-m">
      <h2 data-type="title-m">{view.story.title}</h2><p className="text-on-surface-variant">Story revision {view.session.story_revision} · {view.session.history.length} choices</p>
      <p style={{ whiteSpace: 'pre-wrap' }}>{view.node.text}</p>
      <Narration sessionId={view.session.id} revision={view.session.revision} baseUrl={baseUrl} />
      {view.node.kind === 'ending' && <p role="status">The end</p>}
      {view.node.choices.map(c => <Button key={c.id} disabled={busy} onClick={() => choose(c)}>{c.label}</Button>)}
    </Surface></section> : <form className="space-y-m" onSubmit={e => { e.preventDefault(); void save() }}>
      <fieldset disabled={busy || loading} className="space-y-m">
        <div className="grid gap-m md:grid-cols-2"><Field label="Story title"><TextInput required maxLength={200} value={draft.title} onChange={title => setDraft(d => ({ ...d, title }))} /></Field>
        <Field label="Start scene"><Select value={draft.start_node} onChange={start_node => setDraft(d => ({ ...d, start_node }))} options={draft.nodes.map(n => ({ value: n.id, label: n.id }))} /></Field></div>
        <div className="grid gap-m lg:grid-cols-2">{draft.nodes.map((n, i) => <section key={n.id} aria-label={`Scene ${i + 1}`}><Surface className="h-full space-y-m p-m">
          <h3 data-type="title-m">{n.id}</h3><Field label={`Scene ${i + 1} text`}><textarea aria-label={`Scene ${i + 1} text`} required maxLength={10000} value={n.text} onChange={event => editNode(n.id, { text: event.target.value })} className="min-h-28 w-full resize-y rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" /></Field>
          <Field label={`Scene ${i + 1} kind`}><Select value={n.kind} onChange={kind => editNode(n.id, { kind: kind as Node['kind'], choices: [] })} options={[{ value: 'scene', label: 'Scene' }, { value: 'ending', label: 'Ending' }]} /></Field>
          {n.choices.map((c, j) => <div key={c.id} className="flex flex-wrap gap-2">
            <div className="min-w-0 flex-1"><Field label={`Choice ${i + 1}.${j + 1}`}><TextInput required maxLength={200} value={c.label} onChange={label => editNode(n.id, { choices: n.choices.map(x => x.id === c.id ? { ...x, label } : x) })} /></Field></div>
            <div className="min-w-0 flex-1"><Field label={`Target ${i + 1}.${j + 1}`}><Select value={c.target} onChange={target => editNode(n.id, { choices: n.choices.map(x => x.id === c.id ? { ...x, target } : x) })} options={draft.nodes.map(target => ({ value: target.id, label: target.id }))} /></Field></div>
            <Button type="button" onClick={() => editNode(n.id, { choices: n.choices.filter(x => x.id !== c.id) })}>Remove choice {i + 1}.{j + 1}</Button>
          </div>)}
          {n.kind === 'scene' && <Button type="button" disabled={n.choices.length >= 20} onClick={() => editNode(n.id, { choices: [...n.choices, { id: token(), label: '', target: draft.nodes[0].id }] })}>Add choice to scene {i + 1}</Button>}
          <Button type="button" disabled={draft.nodes.length === 1} onClick={() => setDraft(d => ({ ...d, nodes: d.nodes.filter(x => x.id !== n.id) }))}>Remove scene {i + 1}</Button>
        </Surface></section>)}</div>
        <Button type="button" disabled={draft.nodes.length >= 200} onClick={() => setDraft(d => ({ ...d, nodes: [...d.nodes, { id: token(), text: '', kind: 'ending', choices: [] }] }))}>Add scene</Button>
        <Button type="submit">Save story</Button>
      </fieldset>
    </form>}
    <section aria-label="Saved playthroughs" className="space-y-s"><h2 data-type="title-m">Resume a playthrough</h2>{sessions.length === 0 && <p>No saved playthroughs.</p>}<div className="flex flex-wrap gap-s">{sessions.map(s => <Button key={s.id} variant="secondary" disabled={busy} onClick={() => navigate('session', s.id)}>{stories.find(x => x.id === s.story_id)?.title || 'Saved story'} · {s.current_node} · {s.id.slice(0, 8)}</Button>)}</div></section>
    </>}
  </div></main></div></AreaNavigation>
}
