import { useEffect, useRef, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import './experience.css'
import Narration from './Narration'
import { requestJson } from '../../../shared/data/gatewayRequest'

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
  const navigate = (kind: string, id: string) => { location.hash = `/capabilities/experience?${kind}=${encodeURIComponent(id)}` }
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
  return <main className="experience-page p-4 max-w-4xl mx-auto space-y-4" aria-label="Interactive stories">
    <h1>Interactive stories</h1>
    <p>Author connected scenes and play choices into different endings.</p>
    {loading && <p role="status">Loading stories…</p>}
    {error && <p role="alert">{error}</p>}
    <nav aria-label="Story library" className="flex flex-wrap gap-2">
      <Button disabled={busy} onClick={() => { location.hash = '/capabilities/experience'; setDraft(blank()); setView(null) }}>New story</Button>
      {stories.map(s => <span key={s.id}><Button disabled={busy} onClick={() => navigate('story', s.id)}>{s.title}</Button> <Button disabled={busy} onClick={() => start(s)}>Play {s.title}</Button></span>)}
    </nav>
    {!loading && stories.length === 0 && <p>No stories yet. Write an opening below.</p>}
    {view ? <section aria-label="Story player" className="space-y-3">
      <h2>{view.story.title}</h2><p>Story revision {view.session.story_revision} · {view.session.history.length} choices</p>
      <p style={{ whiteSpace: 'pre-wrap' }}>{view.node.text}</p>
      <Narration sessionId={view.session.id} revision={view.session.revision} baseUrl={baseUrl} />
      {view.node.kind === 'ending' && <p role="status">The end</p>}
      {view.node.choices.map(c => <Button key={c.id} disabled={busy} onClick={() => choose(c)}>{c.label}</Button>)}
    </section> : <form className="space-y-3" onSubmit={e => { e.preventDefault(); void save() }}>
      <fieldset disabled={busy || loading} className="space-y-3">
        <label>Story title<input required maxLength={200} value={draft.title} onChange={e => setDraft(d => ({ ...d, title: e.target.value }))} /></label>
        <label>Start scene<select value={draft.start_node} onChange={e => setDraft(d => ({ ...d, start_node: e.target.value }))}>{draft.nodes.map(n => <option key={n.id} value={n.id}>{n.id}</option>)}</select></label>
        {draft.nodes.map((n, i) => <section key={n.id} aria-label={`Scene ${i + 1}`} className="border rounded p-3 space-y-2">
          <h3>{n.id}</h3><label>Scene {i + 1} text<textarea required maxLength={10000} value={n.text} onChange={e => editNode(n.id, { text: e.target.value })} /></label>
          <label>Scene {i + 1} kind<select value={n.kind} onChange={e => editNode(n.id, { kind: e.target.value as Node['kind'], choices: [] })}><option value="scene">Scene</option><option value="ending">Ending</option></select></label>
          {n.choices.map((c, j) => <div key={c.id} className="flex flex-wrap gap-2">
            <label>Choice {i + 1}.{j + 1}<input required maxLength={200} value={c.label} onChange={e => editNode(n.id, { choices: n.choices.map(x => x.id === c.id ? { ...x, label: e.target.value } : x) })} /></label>
            <label>Target {i + 1}.{j + 1}<select value={c.target} onChange={e => editNode(n.id, { choices: n.choices.map(x => x.id === c.id ? { ...x, target: e.target.value } : x) })}>{draft.nodes.map(target => <option key={target.id} value={target.id}>{target.id}</option>)}</select></label>
            <Button type="button" onClick={() => editNode(n.id, { choices: n.choices.filter(x => x.id !== c.id) })}>Remove choice {i + 1}.{j + 1}</Button>
          </div>)}
          {n.kind === 'scene' && <Button type="button" disabled={n.choices.length >= 20} onClick={() => editNode(n.id, { choices: [...n.choices, { id: token(), label: '', target: draft.nodes[0].id }] })}>Add choice to scene {i + 1}</Button>}
          <Button type="button" disabled={draft.nodes.length === 1} onClick={() => setDraft(d => ({ ...d, nodes: d.nodes.filter(x => x.id !== n.id) }))}>Remove scene {i + 1}</Button>
        </section>)}
        <Button type="button" disabled={draft.nodes.length >= 200} onClick={() => setDraft(d => ({ ...d, nodes: [...d.nodes, { id: token(), text: '', kind: 'ending', choices: [] }] }))}>Add scene</Button>
        <Button type="submit">Save story</Button>
      </fieldset>
    </form>}
    <section aria-label="Saved playthroughs"><h2>Resume a playthrough</h2>{sessions.length === 0 && <p>No saved playthroughs.</p>}{sessions.map(s => <Button key={s.id} disabled={busy} onClick={() => navigate('session', s.id)}>{stories.find(x => x.id === s.story_id)?.title || 'Saved story'} · {s.current_node} · {s.id.slice(0, 8)}</Button>)}</section>
  </main>
}
