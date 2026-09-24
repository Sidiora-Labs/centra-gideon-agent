import { useEffect, useRef, useState } from 'react'
import { api, type InboxSettings } from '../../shared/data/api'
import { notify } from '../../app/shell/appSdk'

type Change<Value> = { id: object; patch: Partial<Value> }
function useOptimisticRecord<Value extends object>() {
  const [value, setValue] = useState<Value | null>(null)
  const baseline = useRef<Value | null>(null)
  const pending = useRef<Change<Value>[]>([])
  const queue = useRef(Promise.resolve())
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const publish = () => { if (mounted.current) setValue(baseline.current && pending.current.reduce((record, change) => ({ ...record, ...change.patch }), baseline.current)) }
  const reset = (record: Value) => { baseline.current = record; publish() }
  const write = (patch: Partial<Value>, request: () => Promise<unknown>, success: () => void, failure: (error: unknown) => void) => {
    if (!baseline.current) return
    const change = { id: {}, patch }; pending.current.push(change); publish()
    queue.current = queue.current.then(async () => {
      try { await request(); baseline.current = { ...baseline.current!, ...patch }; if (mounted.current) success() }
      catch (error) { if (mounted.current) failure(error) }
      finally { pending.current = pending.current.filter(entry => entry.id !== change.id); publish() }
    })
  }
  return { value, reset, write }
}
export function useInboxSettingsState() {
  const settings = useOptimisticRecord<InboxSettings>()
  const flags = useOptimisticRecord<{ engagement: boolean; sources: boolean }>()
  const [loadErr, recordLoadError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  const [cfgErr, setCfgErr] = useState('')
  const [cfgLoading, setCfgLoading] = useState(true)
  const [cfgRevision, setCfgRevision] = useState(0)
  const [revision, setRevision] = useState(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    let current = true
    const setLoadErr = (error: unknown) => { if (current) recordLoadError(error) }
    api.inboxSettings().then(value => { if (current) { settings.reset(value); recordLoadError(null) } }).catch(setLoadErr)
    return () => { current = false }
  }, [revision])
  useEffect(() => {
    let current = true
    api.gideonConfig().then(config => {
      if (current) {
        flags.reset({ engagement: Boolean(config?.inbox?.engagement_ranking_enabled), sources: Boolean(config?.inbox?.enabled) })
        setCfgErr('')
      }
    }).catch(error => { if (current) setCfgErr(String((error as Error)?.message || error)) })
      .finally(() => { if (current) setCfgLoading(false) })
    return () => { current = false; if (timer.current) clearTimeout(timer.current) }
  }, [cfgRevision])
  const retryConfig = () => { setCfgErr(''); setCfgLoading(true); setCfgRevision(value => value + 1) }
  const acknowledge = () => { setSaved(true); if (timer.current) clearTimeout(timer.current); timer.current = setTimeout(() => setSaved(false), 1600) }
  const patch = (change: Partial<InboxSettings>) => settings.write(change, () => api.saveInboxSettings(change), acknowledge, error => notify(`Couldn't save your inbox settings: ${String((error as Error)?.message || error)}`, 'error'))
  const setEngagement = (value: boolean) => flags.write({ engagement: value }, () => api.patchConfig('inbox.engagement_ranking_enabled', value), acknowledge, () => {})
  const setSources = (value: boolean) => flags.write({ sources: value }, async () => { await api.patchConfig('inbox.enabled', value); await api.restartInbox() }, acknowledge, () => {})
  return { s: settings.value, saved, cfgErr, cfgLoading, retryConfig, loadErr, load: () => setRevision(value => value + 1), engagementOn: flags.value?.engagement ?? null, sourcesOn: flags.value?.sources ?? null, patch, setEngagement, setSources }
}
