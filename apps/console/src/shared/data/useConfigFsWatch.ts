import { useEffect, useRef } from 'react'
import { api } from './api'

type ChangeListener = (path: string) => void
const watchers = new Map<string, { stream: EventSource; listeners: Set<ChangeListener> }>()

export function configChangePath(raw: string): string | undefined {
  try {
    const value: unknown = JSON.parse(raw)
    return value && typeof value === 'object' && 'path' in value && typeof value.path === 'string' ? value.path : undefined
  } catch { return undefined }
}

export function watchConfigFiles(listener: ChangeListener): () => void {
  let address: string
  try { address = api.configFsStreamUrl() } catch { return () => {} }
  let watcher = watchers.get(address)
  if (!watcher) {
    let stream: EventSource
    try { stream = new EventSource(address) } catch { return () => {} }
    const listeners = new Set<ChangeListener>()
    watcher = { stream, listeners }
    watchers.set(address, watcher)
    stream.addEventListener('changed', (event) => {
      const path = configChangePath((event as MessageEvent<string>).data)
      if (path === undefined) return
      for (const callback of [...listeners]) {
        try { callback(path) } catch (error) { console.error('Configuration subscriber failed', error) }
      }
    })
  }
  const current = watcher
  current.listeners.add(listener)
  return () => {
    current.listeners.delete(listener)
    if (!current.listeners.size) {
      current.stream.close()
      watchers.delete(address)
    }
  }
}

export function useConfigFsWatch(enabled: boolean, onChange: ChangeListener): void {
  const latest = useRef(onChange)
  latest.current = onChange
  useEffect(() => enabled ? watchConfigFiles((path) => latest.current(path)) : undefined, [enabled])
}
