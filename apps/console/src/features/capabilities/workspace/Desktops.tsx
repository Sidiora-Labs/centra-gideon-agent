import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
type Desktop = { id: string; project_id: string; width: number; height: number; status: string; revision: number }
const base = '/api/capabilities/workspace/desktops'
export default function Desktops() {
  const [rows, setRows] = useState<Desktop[]>([])
  const [projects, setProjects] = useState<{ project: { id: string; name: string } }[]>([])
  const [project, setProject] = useState('')
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('desktop') || '')
  const [available, setAvailable] = useState<boolean | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [text, setText] = useState('')
  const [frame, setFrame] = useState('')
  const retry = useRef<{ project: string; id: string } | null>(null)
  const row = rows.find(item => item.id === selected)
  function choose(id: string) {
    const query = new URLSearchParams(location.hash.split('?')[1] || '')
    query.set('desktop', id); location.hash = `/capabilities/workspace?${query}`; setSelected(id)
  }
  useEffect(() => {
    Promise.all([requestJson<Desktop[]>(base), requestJson<typeof projects>('/api/capabilities/workspace/projects'), requestJson<{ available: boolean }>(`${base}/availability`)]).then(([sessions, list, availability]) => { setRows(sessions); setProjects(list); setAvailable(availability.available) }).catch(e => setError(String(e)))
    const changed = () => setSelected(new URLSearchParams(location.hash.split('?')[1] || '').get('desktop') || '')
    addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed)
  }, [])
  useEffect(() => {
    let active = true, object = '', timer: ReturnType<typeof setTimeout>
    setFrame('')
    async function poll() {
      try {
        const response = await fetch(`${base}/${selected}/frame`, { credentials: 'same-origin' })
        if (!response.ok) throw new Error((await response.json()).error || 'Desktop frame unavailable')
        const blob = await response.blob()
        if (active) { if (object) URL.revokeObjectURL(object); object = URL.createObjectURL(blob); setFrame(object) }
      } catch (e) { if (active) setError(String(e)) }
      if (active) timer = setTimeout(poll, 1100)
    }
    if (row?.status === 'running') void poll()
    return () => { active = false; clearTimeout(timer); if (object) URL.revokeObjectURL(object) }
  }, [selected, row?.status])
  async function act(work: () => Promise<void>) {
    setBusy(true); setError('')
    try { await work() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  async function input(payload: object) { await requestJson(`${base}/${selected}/input`, 'POST', payload) }
  return <section className="space-y-3 border-t border-outline pt-4"><h2 className="text-lg font-semibold">Isolated desktop sessions</h2>
    <p>Open a private Linux desktop for a registered project. Frames refresh at up to one per second. Keyboard and mouse control apply only to this session.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {available === null ? <p role="status">Checking desktop availability…</p> : !available && <p>Isolated desktop dependencies unavailable.</p>}
    <label htmlFor="desktop-project">Desktop project<select aria-label="Desktop project" id="desktop-project" value={project} onChange={e => setProject(e.target.value)}><option value="">Select a project</option>{projects.map(p => <option key={p.project.id} value={p.project.id}>{p.project.name}</option>)}</select></label>
    <Button disabled={busy || !available || !project} onClick={() => void act(async () => { if (retry.current?.project !== project) retry.current = { project, id: crypto.randomUUID() }; const created = await requestJson<Desktop>(base, 'POST', { project_id: project, request_id: retry.current.id, width: 800, height: 600 }); setRows(old => [created, ...old.filter(x => x.id !== created.id)]); retry.current = null; choose(created.id) })}>Start isolated desktop</Button>
    {available && rows.length === 0 && <p>No desktop sessions.</p>}
    <ul>{rows.map(item => <li key={item.id}><Button variant="ghost" onClick={() => choose(item.id)}>{item.project_id} · {item.status}</Button></li>)}</ul>
    {row && <article className="space-y-2"><p>Desktop status: {row.status}</p>
      {row.status === 'running' && <><Button disabled={busy} onClick={() => void act(async () => { const stopped = await requestJson<Desktop>(`${base}/${row.id}/stop`, 'POST', { revision: row.revision }); setRows(old => old.map(x => x.id === row.id ? stopped : x)) })}>Stop isolated desktop</Button>
        {frame ? <img src={frame} alt="Isolated desktop frame" className="max-w-full" onClick={e => { const bounds = e.currentTarget.getBoundingClientRect(); const x = Math.min(row.width - 1, Math.floor((e.clientX - bounds.left) * row.width / bounds.width)), y = Math.min(row.height - 1, Math.floor((e.clientY - bounds.top) * row.height / bounds.height)); void act(() => input({ kind: 'click', x, y })) }} /> : <p role="status">Waiting for desktop frame…</p>}
        <label htmlFor="desktop-text">Desktop text<input id="desktop-text" maxLength={2048} value={text} onChange={e => setText(e.target.value)} /></label>
        <Button disabled={busy || !text} onClick={() => void act(async () => { await input({ kind: 'text', text }); setText('') })}>Send text to desktop</Button>
        <div className="flex flex-wrap gap-2">{['Return', 'Tab', 'Escape', 'BackSpace', 'Left', 'Right', 'Up', 'Down'].map(key => <Button key={key} disabled={busy} onClick={() => void act(() => input({ kind: 'key', key }))}>{key}</Button>)}</div>
      </>}
    </article>}
  </section>
}
