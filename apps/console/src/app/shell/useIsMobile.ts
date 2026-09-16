import { useMemo, useSyncExternalStore } from 'react'

const MOBILE_QUERY = '(max-width: 768px)'
export function useIsMobile(): boolean {
  const viewport = useMemo(() => {
    const query = typeof window === 'undefined' ? undefined : window.matchMedia?.(MOBILE_QUERY)
    return {
      read: () => query?.matches ?? false,
      subscribe: (changed: () => void) => {
        query?.addEventListener('change', changed)
        return () => query?.removeEventListener('change', changed)
      },
    }
  }, [])
  return useSyncExternalStore(viewport.subscribe, viewport.read, () => false)
}
