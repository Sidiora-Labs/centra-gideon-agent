import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Pane = { id: string; window_id: string; tab_id: string; title: string; columns: number; rows: number }
type Screen = Pane & { lines: string[]; truncated: boolean }
const base = '/api/capabilities/workspace/external-terminals'

export default function ExternalTerminals() {
  const [panes, setPanes] = useState<Pane[]>([])
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('pane') || '')
  const [screen, setScreen] = useState<Screen | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [observing, setObserving] = useState(false)
  async function discover() {
    setBusy(true); setError('')
    try { setPanes((await requestJson<{ panes: Pane[] }>(base)).panes) }
    catch (e) { setError(String(e)) }
    finally { setBusy(false) }
  }
  useEffect(() => {
    if (!selected || !observing) return
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function read() {
      try {
        const value = await requestJson<Screen>(`${base}/${encodeURIComponent(selected)}`)
        if (active) { setScreen(value); setError(''); timer = setTimeout(read, 2000) }
      } catch (e) { if (active) { setScreen(null); setError(String(e)); setObserving(false) } }
    }
    void read()
    return () => { active = false; clearTimeout(timer) }
  }, [selected, observing])
  function select(id: string) {
    setSelected(id); setScreen(null); setObserving(false)
    const [path, query = ''] = location.hash.split('?')
    const params = new URLSearchParams(query)
    if (id) params.set('pane', id); else params.delete('pane')
    history.replaceState(null, '', `${location.pathname}${location.search}${path}?${params}`)
  }
  return <section aria-label="External terminal mirror" className="space-y-3 border-t border-border pt-4">
    <h2 className="text-lg font-semibold">External terminal mirror</h2>
    <p>Observe an existing local iTerm pane. Its size and lifecycle stay under native control. Requires macOS and iTerm authorization.</p>
    <Button disabled={busy} onClick={() => void discover()}>{busy ? 'Discovering…' : 'Discover native panes'}</Button>
    <label className="block">Native pane <select aria-label="Native pane" className="max-w-full border rounded p-2 bg-background" value={selected} onChange={e => select(e.target.value)}>
      <option value="">Choose a pane</option>
      {panes.map(p => <option key={p.id} value={p.id}>{p.title} · {p.columns}×{p.rows} · {p.window_id}/{p.tab_id}</option>)}
    </select></label>
    <Button disabled={!selected} onClick={() => setObserving(value => !value)}>{observing ? 'Disconnect mirror' : 'Observe pane'}</Button>
    {error && <p role="alert">{error}</p>}
    {screen && <><p>{screen.columns}×{screen.rows}{screen.truncated ? ' · Display excerpt truncated' : ''}</p><pre aria-label="Native pane output" className="overflow-auto max-h-96 whitespace-pre border rounded p-3">{screen.lines.join('\n')}</pre></>}
  </section>
}
