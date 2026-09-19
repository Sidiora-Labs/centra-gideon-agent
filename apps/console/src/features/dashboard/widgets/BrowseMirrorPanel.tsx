import { useState } from 'react'
import { Check, Globe, ShieldCheck, X } from 'lucide-react'
import { reportingWrite } from '../../../app/shell/reportingWrite'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { useQuery } from '../../../shared/data/data'
import { useVisiblePoll } from '../../../shared/data/useVisiblePoll'
import { RowAction, SlotEmptyState, WidgetRow } from './kit'

const GRANT_POLL_MS = 1000

interface ReachableScope {
  summary: string
  schemes: string[]
  allow_hosts: string[]
  deny_hosts: string[]
  allow_private: boolean
  allow_only: boolean
  loopback_only: boolean
}

interface BrowseGrant {
  request_id: string
  task: string
  scope: string[]
  group: string
  reachable_scope: ReachableScope
}

interface BrowseGrantsResponse { grants: BrowseGrant[] }

const readGrants = () => requestJson<BrowseGrantsResponse>('/api/browse/grants')
const answerGrant = (requestId: string, decision: 'approve' | 'reject') =>
  requestJson<{ ok: true }>(`/api/browse/grants/${encodeURIComponent(requestId)}`, 'POST', { decision })

function requestedSites(grant: BrowseGrant): string {
  return grant.scope.length > 0 ? grant.scope.join(', ') : 'No start site was reported'
}

function effectiveScope(scope: ReachableScope): string {
  const details: string[] = []
  if (scope.allow_hosts.length > 0) details.push(`allowed: ${scope.allow_hosts.join(', ')}`)
  if (scope.deny_hosts.length > 0) details.push(`blocked: ${scope.deny_hosts.join(', ')}`)
  return [scope.summary, ...details].join(' · ')
}

export function BrowseMirrorPanel() {
  const [busy, setBusy] = useState('')
  const { data, error, refresh } = useQuery('browse:grants', readGrants, { persist: false })
  useVisiblePoll(refresh, GRANT_POLL_MS)

  const answer = async (grant: BrowseGrant, decision: 'approve' | 'reject') => {
    setBusy(grant.request_id)
    const verb = decision === 'approve' ? 'approve' : 'reject'
    const ok = await reportingWrite(`${verb} browser access for “${grant.task}”`, () =>
      answerGrant(grant.request_id, decision),
    )
    setBusy('')
    if (ok) refresh()
  }

  if (!data && error) {
    return (
      <SlotEmptyState icon={Globe}>
        Couldn&rsquo;t read browser-control requests. No task can start without an answer.
      </SlotEmptyState>
    )
  }

  const grants = data?.grants ?? []
  if (grants.length === 0) {
    return (
      <SlotEmptyState icon={ShieldCheck}>
        No browser-control request is waiting. Each task needs a fresh human approval before it
        can use your browser.
      </SlotEmptyState>
    )
  }

  return (
    <div className="flex min-w-0 flex-col gap-xs pt-xs" aria-label="Browser-control requests">
      {grants.map((grant) => {
        const working = busy === grant.request_id
        return (
          <WidgetRow
            key={grant.request_id}
            actions={(
              <>
                <RowAction
                  tone="ok"
                  onClick={() => { void answer(grant, 'approve') }}
                  title="Allow only this browser task"
                  ariaLabel={`Allow browser access for ${grant.task}`}
                ><Check size={14} /> Allow this task</RowAction>
                <RowAction
                  tone="danger"
                  onClick={() => { void answer(grant, 'reject') }}
                  title="Reject this browser task"
                  ariaLabel={`Reject browser access for ${grant.task}`}
                ><X size={14} /> Reject</RowAction>
              </>
            )}
          >
            <div className="flex min-w-0 items-start gap-s" aria-busy={working}>
              <ShieldCheck size={15} className="mt-0.5 shrink-0 text-warn" />
              <div className="min-w-0">
                <p data-type="title-m" className="text-on-surface">{grant.task}</p>
                <p data-type="body-s" className="text-on-surface-var">
                  Starts at: {requestedSites(grant)}
                </p>
                <p data-type="caption" className="text-on-surface-low">
                  Reachable scope: {effectiveScope(grant.reachable_scope)}
                </p>
              </div>
            </div>
          </WidgetRow>
        )
      })}
    </div>
  )
}
