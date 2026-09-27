import { useEffect, useMemo, useRef, useState } from 'react'
import type { OwnerScope } from '../../shared/auth.web'
import { WorkspaceFrame, type WorkspaceFrameState } from '../../shared/shell/WorkspaceFrame.web'
import { createShellRoute, type ShellReturnContext, type ShellRoute } from '../../shared/shell/shellRoutes'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { BrowserClient } from './browserClient'
import type { BrowserResult, BrowserSession } from './browserTypes'

export const BROWSER_PLACEMENT = 'browser/session'

export function browserRoute(conversationId: string, returnTo?: ShellReturnContext): ShellRoute {
  if (!conversationId.trim()) throw new TypeError('A canonical conversation ID is required')
  return createShellRoute('apps', { view: 'workspace', placement: { id: BROWSER_PLACEMENT },
    sessionId: conversationId, returnTo: returnTo ?? { destination: 'chat', sessionId: conversationId } })
}

export function browserConversationRoute(route: ShellRoute): ShellRoute {
  const source = route.returnTo
  if (source) return createShellRoute(source.destination, {
    view: source.record ? 'detail' : 'list', record: source.record,
    placement: source.placement, sessionId: source.sessionId,
  })
  return createShellRoute('chat', { sessionId: route.sessionId })
}

export function isBrowserRoute(route: ShellRoute): boolean {
  return route.destination === 'apps' && route.view === 'workspace' &&
    route.placement?.id === BROWSER_PLACEMENT && !!route.sessionId && !route.record
}

type Props = {
  route: ShellRoute
  scope: OwnerScope
  navigate: (route: ShellRoute) => void
  onReturn?: () => void
}

export default function BrowserRoute({ route, scope, navigate, onReturn }: Props) {
  const { palette } = useShellTheme()
  const client = useMemo(() => new BrowserClient(scope), [scope.cacheKey, scope.runtimeOrigin])
  const [result, setResult] = useState<BrowserResult<BrowserSession> | null>(null)
  const [loadedKey, setLoadedKey] = useState('')
  const [busyState, setBusyState] = useState<{ key: string; revision: number } | null>(null)
  const generation = useRef(0)
  const conversationId = isBrowserRoute(route) ? route.sessionId! : ''
  const key = JSON.stringify([scope.cacheKey, conversationId])
  const busy = busyState?.key === key && busyState.revision === generation.current
  const visible = loadedKey === key ? result : null
  const current = visible?.state === 'ready' ? visible.value : visible?.current

  async function load() {
    const revision = ++generation.current
    setLoadedKey(key)
    setResult(null)
    const next = await client.open(conversationId)
    if (generation.current === revision) setResult(next)
  }

  useEffect(() => { void load(); return () => { generation.current++ } }, [client, key])

  async function act(action: 'start' | 'close' | 'reopen') {
    if (!current || busy) return
    const revision = generation.current
    setBusyState({ key, revision })
    const next = await client.mutate(current, action)
    if (generation.current === revision) setResult(next)
    setBusyState(previous => previous?.key === key && previous.revision === revision ? null : previous)
  }

  const back = () => onReturn ? onReturn() : navigate(browserConversationRoute(route))
  let state: WorkspaceFrameState = { kind: 'loading', message: 'Opening conversation browser…' }
  if (!conversationId) state = { kind: 'empty', message: 'Open a saved conversation to use its browser.' }
  else if (visible?.state === 'ready') state = { kind: 'ready' }
  else if (visible?.state === 'denied' || visible?.state === 'signed-out')
    state = { kind: 'denied', message: visible.message }
  else if (visible?.state === 'missing') state = { kind: 'empty', message: visible.message }
  else if (visible) state = { kind: 'error', message: visible.message,
    onRetry: visible.state === 'retryable' || visible.state === 'unavailable' ||
      visible.state === 'disconnected' || visible.state === 'invalid-response' ? () => void load() : undefined }

  return <WorkspaceFrame route={route} mode="full" title="Conversation browser" state={state}
    onBack={back} onGoToChat={back}
    actions={<button type="button" onClick={back}>Return to conversation</button>}>
    {current && <div style={{ padding: 'clamp(16px, 3vw, 32px)', display: 'grid', gap: 16,
      color: palette.text, background: palette.canvas }}>
      <section aria-label="Browser session" style={{ border: `1px solid ${palette.line}`,
        borderRadius: 14, padding: 18, background: palette.card }}>
        <h2>Browser for this conversation</h2>
        <p>Session {current.id}</p>
        <p role="status">{current.status === 'reserved' ? 'Ready to connect' :
          current.status === 'active' ? 'Connected' : current.status === 'closed' ? 'Closed' :
            'Connection error'}. {current.controlHolder === 'customer' ? 'You have control.' : 'Gideon has control.'}</p>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          {current.status === 'reserved' && <button type="button" disabled={busy || visible?.state !== 'ready'}
            onClick={() => void act('start')}>Connect browser</button>}
          {(current.status === 'closed' || current.status === 'error') && <button type="button"
            disabled={busy || visible?.state !== 'ready'} onClick={() => void act('reopen')}>Reopen browser</button>}
          {current.status !== 'closed' && <button type="button" disabled={busy || visible?.state !== 'ready'}
            onClick={() => void act('close')}>Close browser</button>}
          <button type="button" disabled={busy} onClick={() => void load()}>Refresh state</button>
        </div>
        {visible?.state !== 'ready' && <p role="alert">The last operation needs a fresh state check before another action.</p>}
      </section>
    </div>}
  </WorkspaceFrame>
}
