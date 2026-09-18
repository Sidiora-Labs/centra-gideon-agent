import { useState, useSyncExternalStore, type ReactNode } from 'react'
import { Globe, KeyRound } from 'lucide-react'
import { api } from '../../../shared/data/api'
import { useQuery } from '../../../shared/data/data'
import { useChatSocket, type WsMessage } from '../../../shared/data/useChatSocket'
import { useVisiblePoll } from '../../../shared/data/useVisiblePoll'
import { reportingWrite } from '../../../app/shell/reportingWrite'
import { confirm } from '../../../shared/ui/dialog'
import { Button } from '../../../shared/ui/Button'
import { StatusPill } from '../../../shared/ui/StatusPill'
import { TextLink } from '../../../shared/ui/TextLink'
import { SlotEmptyState } from './kit'
import {
  BROWSE_AUTH_EXPIRED, BROWSE_KILL, BROWSE_STEP,
  applyBrowseAuthExpired, applyBrowseKill, applyBrowseStatus, applyBrowseStep,
  beginBrowseStatusRead, browseMirrorSnapshot, subscribeBrowseMirror,
} from './browseMirrorState'

const STATUS_POLL_MS = 30000

const STOP_REASON = 'Emergency stop from the dashboard'

function screenshotName(path: string): string {
  const cut = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return cut === -1 ? path : path.slice(cut + 1)
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-baseline gap-s">
      <dt data-type="caption" className="w-24 shrink-0 text-on-surface-low">{label}</dt>
      <dd data-type="body-s" className="m-0 min-w-0 flex-1 truncate text-on-surface">{children}</dd>
    </div>
  )
}

export function BrowseMirrorPanel() {
  const [busy, setBusy] = useState('')
  const { step, kill, expired } = useSyncExternalStore(
    subscribeBrowseMirror, browseMirrorSnapshot, browseMirrorSnapshot,
  )
  const { data, error, refresh } = useQuery('browse:status', async () => {
    const read = beginBrowseStatusRead()
    const status = await api.browseStatus()
    applyBrowseStatus(status, read)
    return status
  })

  useChatSocket((message: WsMessage) => {
    if (message.type === BROWSE_STEP) applyBrowseStep(message.data)
    else if (message.type === BROWSE_KILL) applyBrowseKill(message.data)
    else if (message.type === BROWSE_AUTH_EXPIRED) { applyBrowseAuthExpired(message.data); refresh() }
  }, refresh)
  useVisiblePoll(refresh, STATUS_POLL_MS)

  const stop = async () => {
    setBusy('stop')
    const ok = await reportingWrite('stop automated browsing', async () => {
      applyBrowseKill((await api.browseKill(STOP_REASON)).kill)
    })
    setBusy('')
    if (ok) refresh()
  }

  const resume = async () => {
    if (!(await confirm({
      title: 'Resume automated browsing?',
      body: 'Unattended browse runs will be able to open pages again. Anything you stopped them '
        + 'from doing is not undone, and interactive chat was never affected.',
      confirmLabel: 'Resume unattended browsing',
    }))) return
    setBusy('resume')
    const ok = await reportingWrite('resume automated browsing', async () => {
      applyBrowseKill((await api.browseKillRelease()).kill)
    })
    setBusy('')
    if (ok) refresh()
  }

  if (!data && error) {
    return (
      <SlotEmptyState icon={Globe}>
        Couldn&rsquo;t read the browser automation status.
      </SlotEmptyState>
    )
  }

  return (
    <div className="flex min-w-0 flex-col gap-s pt-xs">
      <div className="flex flex-wrap items-center gap-m">
        <StatusPill tone={kill.active ? 'danger' : 'ok'}>
          {kill.active ? 'Stopped' : 'Browsing allowed'}
        </StatusPill>
        {kill.active && kill.reason && (
          <span data-type="caption" className="min-w-0 truncate text-on-surface-low">{kill.reason}</span>
        )}
        <span className="flex-1" />
        {kill.active ? (
          <Button size="xs" variant="secondary" loading={busy === 'resume'} onClick={resume}
            title="Re-enable unattended browsing. You are asked to confirm first.">
            Resume browsing
          </Button>
        ) : (
          <Button size="xs" variant="danger" loading={busy === 'stop'} onClick={stop}
            title="Park every unattended browse run within one step. Interactive chat keeps working.">
            Emergency stop
          </Button>
        )}
      </div>

      {kill.active && (
        <p data-type="body-s" className="m-0 rounded-lg bg-danger/10 px-m py-s text-on-surface-var">
          Unattended browsing is stopped — a running loop parks within one step and a new run
          refuses to start. Interactive chat is untouched.
        </p>
      )}

      {step ? (
        <dl className="m-0 flex min-w-0 flex-col gap-xs">
          <Field label="Address">
            <span title={step.url}>{step.url || 'not reported'}</span>
          </Field>
          <Field label="Latest action">
            {`Step ${step.step_n}: ${step.action || 'not reported'}`}
            {step.note ? ` — ${step.note}` : ''}
          </Field>
          <Field label="Screenshot">
            {step.screenshot ? (
              <TextLink href={api.fileRawUrl(step.screenshot)} external size="sm" title={step.screenshot}>
                {screenshotName(step.screenshot)}
              </TextLink>
            ) : (
              <span className="text-on-surface-low">not captured for this step</span>
            )}
          </Field>
        </dl>
      ) : (
        <SlotEmptyState icon={Globe}>
          No browse step has been mirrored yet. The address, the action and its screenshot appear
          here as each step of a run completes.
        </SlotEmptyState>
      )}

      {expired.length > 0 && (
        <ul aria-label="Browser sign-ins that expired" className="m-0 flex list-none flex-col gap-xs p-0">
          {expired.map((site) => (
            <li key={site.site} className="flex min-w-0 flex-wrap items-center gap-s rounded-lg bg-warn/10 px-m py-s">
              <KeyRound size={14} className="shrink-0 text-warn" />
              <span data-type="body-s" className="min-w-0 text-on-surface">
                Sign-in expired for {site.site}
              </span>
              {site.key_present !== undefined && (
                <span data-type="caption" className="min-w-0 text-on-surface-low">
                  {site.key_present
                    ? 'sign in again in the handoff window and the saved profile is reused'
                    : 'sign in again in the handoff window and a new profile is created'}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
