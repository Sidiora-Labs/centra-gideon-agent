import { Database } from 'lucide-react'
import type { RouteProps } from '../../app/shell/useQueryState'
import { PageTitle } from '../../shared/ui/PageTitle'
import { TopBar } from '../../shared/ui/TopBar'
import { HypermidPanel } from '../settings/HypermidPanel'

export function HypermidPage(_props: RouteProps) {
  return <div className="flex h-full min-w-0 flex-col">
    <TopBar left={<div className="flex min-w-0 items-center gap-s">
      <Database size={18} aria-hidden className="shrink-0 text-on-surface-var" />
      <PageTitle>Hypermid</PageTitle>
    </div>} />
    <div tabIndex={0} role="region" aria-label="Hypermid administration" className="min-w-0 flex-1 overflow-y-auto focus-visible:-outline-offset-2">
      <div className="mx-auto min-w-0 px-l py-l pb-2xl" style={{ maxWidth: 'var(--content-width)' }}>
        <HypermidPanel />
      </div>
    </div>
  </div>
}
