import type { ReactNode } from 'react'
import { InlineError } from '../../shared/ui/InlineError'
import { Loading } from '../../shared/ui/ListScaffold'

export function InboxConfigBoundary({ cfgErr, loading, onRetry, children }: {
  cfgErr: string; loading: boolean; onRetry: () => void; children: ReactNode
}) {
  if (cfgErr) return <InlineError icon onRetry={onRetry}>Couldn't read your inbox configuration: {cfgErr}</InlineError>
  if (loading) return <Loading what="inbox configuration" />
  return <>{children}</>
}
