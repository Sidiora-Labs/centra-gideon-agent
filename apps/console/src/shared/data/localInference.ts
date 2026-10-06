import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type LocalInferenceWait } from './api'
import { gatewayEvents } from './socketTransport'
import { useVisiblePoll } from './useVisiblePoll'

export function useLocalInferenceWaits() {
  const [waits, setWaits] = useState<LocalInferenceWait[]>([])
  const [connected, setConnected] = useState(false)
  const live = useRef(false), revision = useRef(0)
  const refresh = useCallback(() => {
    const observed = ++revision.current
    void api.localInferenceWaits().then(result => {
      if (live.current && observed === revision.current) setWaits(result.waits)
    }).catch(() => { if (live.current && observed === revision.current) setWaits([]) })
  }, [])
  useEffect(() => {
    live.current = true
    const detach = gatewayEvents().attach({
      message: message => { if (message.type === 'local_inference_waits') refresh() },
      reconnect: refresh,
      status: setConnected,
    })
    refresh()
    return () => { live.current = false; ++revision.current; detach() }
  }, [refresh])
  useVisiblePoll(refresh, connected && waits.length === 0 ? null : waits.length ? 1000 : 15000)
  return { waits, refresh }
}
