import { useEffect, useRef, useState } from 'react'
import { requestJson, gatewayHeaders } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { TerminalView } from '../../terminal/TerminalView'
import type { TermTab } from '../../terminal/TerminalPage'

type Profile = { id: string; available: boolean; reason: string | null }
export default function ProviderTerminals() {
  const terminalHost = useRef<HTMLDivElement>(null)
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [profile, setProfile] = useState('')
  const [cwd, setCwd] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [tab, setTab] = useState<TermTab | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { requestJson<{ profiles: Profile[] }>('/api/capabilities/workspace/provider-terminals').then(r => setProfiles(r.profiles)).catch(e => setError(String(e))) }, [])
  useEffect(() => { if (tab) terminalHost.current?.scrollIntoView({ block: 'nearest' }) }, [tab])
  async function launch() {
    setBusy(true); setError('')
    try {
      let image: { artifact_id: string; version: number } | undefined
      if (file) {
        const response = await fetch('/api/capabilities/workspace/provider-terminals/images', { method: 'POST', headers: gatewayHeaders, body: file })
        const value = await response.json()
        if (!response.ok) throw new Error(value.error || 'Image upload failed')
        image = { artifact_id: value.artifact_id, version: value.version }
      }
      const result = await requestJson<{ session_id: string; cwd: string; shell: string }>('/api/terminal/sessions', 'POST', { cwd, provider_id: profile, ...(image ? { image } : {}) })
      setTab({ id: result.session_id, label: profile, cwd: result.cwd, shell: result.shell })
    } catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  async function close() {
    if (!tab) return
    try { await requestJson(`/api/terminal/sessions/${tab.id}`, 'DELETE'); setTab(null) }
    catch (e) { setError(String(e)) }
  }
  return <section aria-label="Provider terminal" className="space-y-3 border-t border-border pt-4">
    <h2 className="text-lg font-semibold">Provider terminal</h2>
    <p>Launch a configured local CLI. Images are retained as artifacts and passed to supported engines at startup. Provider sign-in remains in its terminal.</p>
    <label className="block">Provider <select aria-label="Terminal provider" value={profile} onChange={e => setProfile(e.target.value)} className="border rounded bg-background p-2 max-w-full">
      <option value="">Choose a configured engine</option>{profiles.map(p => <option key={p.id} value={p.id} disabled={!p.available}>{p.id}{p.available ? '' : ` · ${p.reason}`}</option>)}
    </select></label>
    {!profiles.length && <p>No configured interactive provider engines.</p>}
    <label className="block">Working directory <input aria-label="Provider working directory" value={cwd} onChange={e => setCwd(e.target.value)} className="border rounded bg-background p-2 max-w-full" /></label>
    <label className="block">Startup image <input aria-label="Provider startup image" type="file" accept="image/png,image/jpeg,image/webp" onChange={e => setFile(e.target.files?.[0] || null)} className="max-w-full" /></label>
    <Button disabled={!profile || busy || Boolean(tab)} onClick={() => void launch()}>{busy ? 'Launching…' : 'Launch provider terminal'}</Button>
    {error && <p role="alert">{error}</p>}
    {tab && <><Button onClick={() => void close()}>Close provider terminal</Button><div ref={terminalHost} style={{ height: 400 }}><TerminalView tab={tab} onExited={() => setError('Provider process exited')} onClose={() => void close()} /></div></>}
  </section>
}
