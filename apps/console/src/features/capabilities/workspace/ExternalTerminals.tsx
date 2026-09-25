import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, Select } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'

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
  return <section aria-label="External terminal mirror" className="mx-auto w-full space-y-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
    <header><h2 data-type="title-m">External terminal mirror</h2>
    <p data-type="body-s" className="mt-1 text-on-surface-low">Observe an existing local iTerm pane. Its size and lifecycle stay under native control. Requires macOS and iTerm authorization.</p></header>
    <Surface className="space-y-m p-l"><div className="flex flex-wrap gap-s"><Button disabled={busy} onClick={() => void discover()}>{busy ? 'Discovering…' : 'Discover native panes'}</Button><Button variant="secondary" disabled={!selected} onClick={() => setObserving(value => !value)}>{observing ? 'Disconnect mirror' : 'Observe pane'}</Button></div>
    <Field label="Native pane"><Select ariaLabel="Native pane" value={selected} onChange={select} options={[{ value: '', label: 'Choose a pane' }, ...panes.map(p => ({ value: p.id, label: `${p.title} · ${p.columns}×${p.rows} · ${p.window_id}/${p.tab_id}` }))]}/></Field></Surface>
    {error && <p role="alert">{error}</p>}
    {screen && <Surface className="space-y-m p-l"><p data-type="body-s" className="text-on-surface-low">{screen.columns}×{screen.rows}{screen.truncated ? ' · Display excerpt truncated' : ''}</p><pre aria-label="Native pane output" className="max-h-96 overflow-auto whitespace-pre rounded-lg bg-surface p-m">{screen.lines.join('\n')}</pre></Surface>}
  </section>
}
