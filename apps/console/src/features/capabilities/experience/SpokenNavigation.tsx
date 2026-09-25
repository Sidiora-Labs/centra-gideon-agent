import { useEffect, useRef, useState } from 'react'
import { api } from '../../../shared/data/api'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
import { MicCaptureChip } from '../../../shared/ui/MicCaptureChip'
import { useMicRecorder } from '../../../shared/ui/composer/useMicRecorder'
import { resolveNavigation, type NavigationItem } from './navigation'

type Receipt = { id: string; target: string; status: 'requested' | 'applied'; revision: number }
export type SpokenNavigationProps = { items: readonly NavigationItem[]; navigate: (path: string) => void; currentRoute: string; baseUrl?: string }
export default function SpokenNavigation({ items, navigate, currentRoute, baseUrl = '/api/capabilities/experience' }: SpokenNavigationProps) {
  const [command, setCommand] = useState('')
  const [origin, setOrigin] = useState<'typed' | 'voice'>('typed')
  const [receipt, setReceipt] = useState<Receipt | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const request = useRef<{ input: string; id: string } | null>(null)
  const mounted = useRef(true)
  const liveItems = useRef(items)
  liveItems.current = items
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const mic = useMicRecorder(async blob => {
    const result = await api.transcribeAudio(blob)
    if (result.error) throw new Error(result.error)
    return result.text || ''
  }, text => { setCommand(text); setOrigin('voice') }, setError)
  useEffect(() => {
    if (!receipt || receipt.status !== 'requested' || currentRoute !== receipt.target) return
    let active = true
    requestJson<{ receipt: Receipt }>(`${baseUrl}/navigation/${receipt.id}/acknowledge`, 'POST', { revision: 1, observed_route: currentRoute }).then(result => {
      if (active) { setReceipt(result.receipt); setBusy(false) }
    }).catch(cause => { if (active) { setError(String(cause)); setBusy(false) } })
    return () => { active = false }
  }, [receipt, currentRoute, baseUrl])
  const submit = async () => {
    if (busy) return
    setError('')
    try {
      const selected = resolveNavigation(command, items)
      const identity = JSON.stringify([command, origin, currentRoute, selected.id])
      if (request.current?.input !== identity) request.current = { input: identity, id: crypto.randomUUID().replaceAll('-', '') }
      setBusy(true)
      const result = await requestJson<{ receipt: Receipt }>(`${baseUrl}/navigation`, 'POST', { command, input_origin: origin, origin: currentRoute, target: selected.id, request_id: request.current.id })
      if (!mounted.current) return
      if (resolveNavigation(command, liveItems.current).id !== selected.id) throw new Error('Destination changed; choose again.')
      setReceipt(result.receipt)
      if (result.receipt.status === 'applied') setBusy(false)
      else navigate(result.receipt.target)
    } catch (cause) { if (mounted.current) { setError(String(cause)); setBusy(false) } }
  }
  return <Surface tone="low" className="p-m"><section aria-label="Spoken navigation" className="space-y-m">
    <form onSubmit={event => { event.preventDefault(); void submit() }} className="grid min-w-0 gap-s md:grid-cols-[minmax(0,1fr)_auto_auto] md:items-end">
      <Field label="Navigation command"><TextInput maxLength={400} value={command} disabled={busy} onChange={value => { setCommand(value); setOrigin('typed') }} /></Field>
      <Button type="submit" disabled={busy || !command.trim()}>Go</Button>
      <Button disabled={busy || mic.state === 'transcribing'} onClick={() => void mic.toggle()}>{mic.listening ? 'Stop listening' : 'Use microphone'}</Button>
      {mic.listening && <MicCaptureChip onStop={() => void mic.toggle()} />}
    </form>
    {receipt?.status === 'requested' && <Button onClick={() => { setReceipt(null); setBusy(false); request.current = null }}>Stop waiting</Button>}
    {mic.state === 'transcribing' && <p role="status">Transcribing navigation command…</p>}
    {receipt && <p role="status">{receipt.status === 'applied' ? `Opened ${receipt.target}` : `Waiting for ${receipt.target} to open`}</p>}
    {error && <p role="alert">{error}</p>}
  </section></Surface>
}
