import { useEffect, useRef, useState } from 'react'
import { requestJson, gatewayHeaders } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, Select, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
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
  return <section aria-label="Provider terminal" className="mx-auto w-full space-y-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
    <header><h2 data-type="title-m">Provider terminal</h2>
    <p data-type="body-s" className="mt-1 text-on-surface-low">Launch a configured local CLI. Images are retained as artifacts and passed to supported engines at startup. Provider sign-in remains in its terminal.</p></header>
    <Surface className="space-y-m p-l"><Field label="Provider"><Select ariaLabel="Terminal provider" value={profile} onChange={setProfile} options={[{ value: '', label: 'Choose a configured engine' }, ...profiles.map(p => ({ value: p.id, label: `${p.id}${p.available ? '' : ` · ${p.reason}`}`, disabled: !p.available }))]}/></Field>
    {!profiles.length && <p>No configured interactive provider engines.</p>}
    <Field label="Working directory"><TextInput ariaLabel="Provider working directory" value={cwd} onChange={setCwd}/></Field>
    <Field label="Startup image"><input aria-label="Provider startup image" type="file" accept="image/png,image/jpeg,image/webp" onChange={e => setFile(e.target.files?.[0] || null)} className="block w-full rounded-md border border-outline-variant/30 bg-surface px-m py-s text-sm text-on-surface file:me-m file:rounded-md file:border-0 file:bg-surface-high file:px-m file:py-s" /></Field>
    <Button disabled={!profile || busy || Boolean(tab)} onClick={() => void launch()}>{busy ? 'Launching…' : 'Launch provider terminal'}</Button>
    </Surface>
    {error && <p role="alert">{error}</p>}
    {tab && <Surface className="space-y-m p-l"><Button variant="secondary" onClick={() => void close()}>Close provider terminal</Button><div ref={terminalHost} className="min-h-0 overflow-hidden rounded-lg" style={{ height: 400 }}><TerminalView tab={tab} onExited={() => setError('Provider process exited')} onClose={() => void close()} /></div></Surface>}
  </section>
}
