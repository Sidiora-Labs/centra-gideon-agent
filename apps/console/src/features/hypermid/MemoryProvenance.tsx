import { AlertTriangle, BadgeCheck, Database } from 'lucide-react'

export type MemoryProvenanceView = {
  memoryId: string
  sourceType: string
  sourceId: string
  authorPrincipalId: string
  contentDigest: string
  revision: number
  verification: 'unverified' | 'verified' | 'rejected'
  instructionShaped: boolean
  trustClass: 'data' | 'privileged_instruction'
  reviewerPrincipalId?: string
}

type Props = {
  memory: MemoryProvenanceView
  busy?: boolean
  onPromote?: (memoryId: string, expectedRevision: number) => void
}

export function MemoryProvenance({ memory, busy = false, onPromote }: Props) {
  const privileged = memory.trustClass === 'privileged_instruction'
  return (
    <section aria-labelledby={`memory-provenance-${memory.memoryId}`} className="rounded-lg border border-outline-variant bg-surface-container p-l">
      <header className="flex items-start justify-between gap-m">
        <div className="flex min-w-0 items-start gap-s">
          {privileged ? <BadgeCheck className="mt-0.5 size-5 shrink-0 text-success" aria-hidden /> : <Database className="mt-0.5 size-5 shrink-0 text-on-surface-low" aria-hidden />}
          <div className="min-w-0">
            <h3 data-type="title-m" id={`memory-provenance-${memory.memoryId}`} className="font-semibold text-on-surface">Memory provenance</h3>
            <p data-type="caption" className="truncate text-on-surface-low">{memory.sourceType} · {memory.sourceId}</p>
          </div>
        </div>
        <span data-type="caption" className="rounded-full bg-surface px-s py-xs text-on-surface-low">Revision {memory.revision}</span>
      </header>
      {memory.instructionShaped && !privileged && (
        <div data-type="body-s" role="alert" className="mt-m flex gap-s rounded-lg bg-warn/10 p-m text-on-surface">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          <span>This memory resembles instructions. It remains data and cannot change approvals, capabilities, providers, routes, credentials, or budgets.</span>
        </div>
      )}
      <dl data-type="body-s" className="mt-m grid gap-x-m gap-y-s sm:grid-cols-[10rem_minmax(0,1fr)]">
        <dt className="text-on-surface-low">Author</dt><dd className="truncate text-on-surface">{memory.authorPrincipalId}</dd>
        <dt className="text-on-surface-low">Verification</dt><dd className="text-on-surface">{memory.verification}</dd>
        <dt className="text-on-surface-low">Trust</dt><dd className="text-on-surface">{privileged ? 'User reviewed instruction' : 'Retrieved data'}</dd>
        {memory.reviewerPrincipalId && <><dt className="text-on-surface-low">Reviewed by</dt><dd className="truncate text-on-surface">{memory.reviewerPrincipalId}</dd></>}
        <dt className="text-on-surface-low">SHA-256</dt><dd data-type="caption" className="break-all font-mono text-on-surface-low">{memory.contentDigest}</dd>
      </dl>
      {memory.instructionShaped && !privileged && onPromote && (
        <div className="mt-m flex justify-end">
          <button data-type="label-s" type="button" disabled={busy} onClick={() => onPromote(memory.memoryId, memory.revision)} className="rounded-md border border-outline-variant px-m py-s text-on-surface disabled:opacity-50">
            Review for promotion
          </button>
        </div>
      )}
    </section>
  )
}
