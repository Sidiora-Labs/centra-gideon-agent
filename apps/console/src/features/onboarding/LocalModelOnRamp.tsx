import { useState } from 'react'
import { Cpu, Search } from 'lucide-react'
import { api, type LocalModelEndpoint } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { Button } from '../../shared/ui/Button'
import { LoadError } from '../../shared/ui/ListScaffold'

export function LocalModelOnRamp({ bindChat, chatModel = '', onBound }: { bindChat: boolean; chatModel?: string; onBound?: () => void }) {
  const { data, error, refresh } = useQuery('onboarding:local-model', () => api.detectLocalModel())
  const [found, setFound] = useState<LocalModelEndpoint[]>([])
  const [added, setAdded] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(''), [scanState, setScanState] = useState<'idle' | 'scanning' | 'done'>('idle')
  const [failure, setFailure] = useState('')
  const rows = data?.detected && data.endpoint && data.model
    ? [{ endpoint: data.endpoint, model: data.model, provider: data.provider }, ...found.filter(row => row.endpoint !== data.endpoint)] : found
  const scan = async () => {
    setScanState('scanning'); setFailure('')
    try { setFound((await api.scanLocalModels()).endpoints); setScanState('done') }
    catch (error) { setFailure(String((error as Error)?.message || error)); setScanState('idle') }
  }
  const setup = async (row: LocalModelEndpoint) => {
    setBusy(row.endpoint); setFailure('')
    try {
      const result = await api.bindLocalModel(row.endpoint, bindChat)
      if (!result.ok) throw new Error('The local provider could not be set up.')
      setAdded(previous => ({ ...previous, [row.endpoint]: result.provider }))
      for (const key of ['onboarding:local-model', 'onboarding:model-providers', 'settings:providers', 'settings:model-connections', 'settings:models', 'settings:models-available', 'onboarding:local-chat-catalog']) invalidateKeys(key)
      refresh()
      if (bindChat) onBound?.()
    } catch (error) { setFailure(String((error as Error)?.message || error)) }
    finally { setBusy('') }
  }
  return <section aria-label={bindChat ? 'Connect a local model' : 'Also add a local model'} className="grid gap-s rounded-xl border border-outline-variant/40 p-m">
    <h3 className="flex items-center gap-s text-[0.875rem]"><Cpu size={14} aria-hidden />{bindChat ? 'Connect a local model' : 'Also add a local model'}</h3>
    {!bindChat && <p className="text-on-surface-low text-[0.8125rem]">Adds another provider and keeps {chatModel || 'the verified model'} as your chat model. No use case changes.</p>}
    {!!error && <LoadError what="local model detection" error={error} onRetry={refresh} />}
    {rows.map(row => <div key={row.endpoint} className="flex flex-wrap items-center justify-between gap-s rounded-lg bg-surface-high p-s">
      <div><strong>{row.model}</strong><p className="text-on-surface-low text-[0.75rem]">{row.endpoint}</p></div>
      {row.provider || added[row.endpoint] ? <span role="status" className="text-[0.8125rem]">Added as {row.provider || added[row.endpoint]}</span>
        : <Button variant="secondary" size="sm" disabled={!!busy} loading={busy === row.endpoint} onClick={() => void setup(row)}>{bindChat ? 'Use this model' : 'Add this model'}</Button>}
    </div>)}
    {data && !rows.length && <p className="text-on-surface-low text-[0.8125rem]">No local chat model was found. Containers see their own loopback; configure your host endpoint in Settings → Providers if the scan cannot reach it.</p>}
    <Button variant="ghost" size="sm" loading={scanState === 'scanning'} disabled={scanState === 'scanning'} onClick={() => void scan()}><Search size={13} aria-hidden />Scan my local network</Button>
    {scanState === 'done' && !found.length && <p role="status" className="text-on-surface-low text-[0.8125rem]">No model server answered the scan. You can add an endpoint in Settings → Providers.</p>}
    {failure && <p role="alert" className="text-danger text-[0.8125rem]">{failure}</p>}
  </section>
}

export function ModelProviderRecap() {
  const { data, error, refresh } = useQuery('onboarding:model-providers', () => api.modelProviders())
  if (error) return <LoadError what="configured model providers" error={error} onRetry={refresh} />
  if (!data?.length) return null
  return <div className="grid gap-s"><p className="text-on-surface-low text-[0.8125rem]">Model providers set up</p><ul>{data.map(provider => <li key={provider.name} className="text-[0.8125rem]">{provider.name}{provider.model ? ` · ${provider.model}` : ''}</li>)}</ul></div>
}
