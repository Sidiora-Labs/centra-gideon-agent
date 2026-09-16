import { useEffect, useRef } from 'react'
import { api } from '../../../shared/data/api'

export function useFileWatch(path: string | null, enabled: boolean, onChange: (content: string) => void) {
  const cbRef = useRef(onChange)
  cbRef.current = onChange

  useEffect(() => {
    if (!enabled || !path) return
    let es: EventSource | null = null
    try {
      es = new EventSource(api.fileWatchUrl(path, true))
    } catch {
      return
    }
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (typeof data.content === 'string') cbRef.current(data.content)
      } catch {   }
    }
    es.onerror = () => {   }
    return () => { es?.close() }
  }, [path, enabled])
}
