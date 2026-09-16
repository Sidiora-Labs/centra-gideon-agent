import { useQuery } from '../../shared/data/data'
import { api } from '../../shared/data/api'

async function gatewayPlatform(): Promise<string> {
  try { return (await api.system()).platform || '' } catch { return '' }
}
const platformQuery = { key: 'system:platform', read: gatewayPlatform, options: { persist: true } } as const
export function usePlatform(): string {
  return useQuery(platformQuery.key, platformQuery.read, platformQuery.options).data ?? ''
}
export function useIsMac(): boolean { return usePlatform() === 'darwin' }
