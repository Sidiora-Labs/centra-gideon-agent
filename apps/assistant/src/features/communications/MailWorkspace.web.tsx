import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createShellRoute, type ShellRoute } from '../../shared/shell/shellRoutes'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import type { ModuleProps as ShellModuleProps } from '../../shared/shell/webModules.web'
import type { OwnerScope } from '../../shared/auth.web'
import { useShellTheme } from '../../shared/shell/shellTheme'
import { createCommunicationClient } from './communicationClient'
import { connectionReadiness, mirrorReadiness, providerItemKey, type CommunicationItem, type MirrorAccount, type MirrorMessage, type OutboundDraftItem, type OutboundDraftInput, type OutboundDraftEditInput, type ReadSnapshot } from './types'
import { MailDetail } from './MailDetail.web'

type ModuleProps = ShellModuleProps
type MailTab = 'all' | 'unread' | 'drafts'
type Composer = Readonly<{ source?: MirrorMessage; editDraft?: OutboundDraftItem }>
type ArtifactChoice = Readonly<{ slug: string; name: string; version: number }>

const panel: React.CSSProperties = { background: 'var(--mail-card)', border: '1px solid var(--mail-line)', borderRadius: 16, padding: 18, color: 'var(--mail-text)' }
const button: React.CSSProperties = { border: '1px solid var(--mail-line)', background: 'var(--mail-card)', color: 'var(--mail-text)', borderRadius: 10, padding: '9px 13px', font: 'inherit', cursor: 'pointer' }
const primary: React.CSSProperties = { ...button, background: 'var(--mail-green)', borderColor: 'var(--mail-line)' }
const field: React.CSSProperties = { boxSizing: 'border-box', width: '100%', border: '1px solid var(--mail-line)', borderRadius: 9, padding: '10px 12px', font: 'inherit', color: 'var(--mail-text)', background: 'var(--mail-card)' }
const subtle: React.CSSProperties = { color: 'var(--mail-muted)', fontSize: 13 }

function placementFor(accountId?: string) {
  return { id: 'capabilities/communications/outbound', query: accountId ? { account: accountId } : undefined }
}

export function filterMailItems<T extends CommunicationItem<MirrorMessage>>(items: readonly T[], query: string): T[] {
  const needle = query.trim().toLocaleLowerCase()
  if (!needle) return [...items]
  return items.filter(({ value }) => [value.subject, value.body, ...value.sender, ...value.recipients]
    .some(text => text.toLocaleLowerCase().includes(needle)))
}

export function unreadMailItems<T extends CommunicationItem<MirrorMessage>>(items: readonly T[]): T[] {
  return items.filter(item => !item.value.is_read)
}

export function mailDraftRouteId(item: OutboundDraftItem): string {
  return providerItemKey(item.identity)
}

function selectedAccountFromRoute(route: ShellRoute, scope: OwnerScope): string | undefined {
  const selected = route.placement?.query?.account
  if (selected) return selected
  if (!route.record || !['mail-message', 'mail-draft'].includes(route.record.kind)) return undefined
  try {
    const identity: unknown = JSON.parse(route.record.id)
    if (Array.isArray(identity) && identity[0] === scope.cacheKey && typeof identity[1] === 'string') return identity[1]
  } catch { return undefined }
  return undefined
}

export function MailWorkspace({ route, scope, navigate, onReturn, returnTo }: ModuleProps) {
  const { palette } = useShellTheme()
  const client = useMemo(() => createCommunicationClient(scope), [scope.cacheKey])
  const [accounts, setAccounts] = useState<readonly MirrorAccount[]>([])
  const [account, setAccount] = useState<MirrorAccount | null>(null)
  const [loadedScopeKey, setLoadedScopeKey] = useState('')
  const [snapshot, setSnapshot] = useState<ReadSnapshot<readonly CommunicationItem<MirrorMessage>[]> | null>(null)
  const [drafts, setDrafts] = useState<readonly OutboundDraftItem[]>([])
  const [query, setQuery] = useState('')
  const [tab, setTab] = useState<MailTab>('all')
  const [loading, setLoading] = useState(true)
  const [accountError, setAccountError] = useState('')
  const [mailError, setMailError] = useState('')
  const [composer, setComposer] = useState<Composer | null>(null)
  const [to, setTo] = useState('')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [artifactIds, setArtifactIds] = useState('')
  const [artifactChoices, setArtifactChoices] = useState<readonly ArtifactChoice[]>([])
  const [artifactLoading, setArtifactLoading] = useState(false)
  const [artifactError, setArtifactError] = useState('')
  const [busy, setBusy] = useState(false)
  const [readBusy, setReadBusy] = useState(false)
  const [draft, setDraft] = useState<OutboundDraftItem | null>(null)
  const [notice, setNotice] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const listFocusRef = useRef<HTMLButtonElement | null>(null)
  const loadEpoch = useRef(0)
  const loadedScopeRef = useRef('')
  const loadedAccountRef = useRef<string | null>(null)

  const desiredAccountId = selectedAccountFromRoute(route, scope)
  const accountMatchesRoute = !!account && (!desiredAccountId || desiredAccountId === account.id)
  const accountMatchesScope = loadedScopeKey === scope.cacheKey
  const currentContext = useRef({ ownerScopeKey: scope.cacheKey, desiredAccountId })
  currentContext.current = { ownerScopeKey: scope.cacheKey, desiredAccountId }

  function captureAction(accountId: string) {
    const token = client.captureSelectionToken()
    if (token.accountId !== accountId || currentContext.current.ownerScopeKey !== scope.cacheKey ||
        (currentContext.current.desiredAccountId && currentContext.current.desiredAccountId !== accountId)) {
      throw new Error('Mailbox selection changed. Refresh the selected account before continuing.')
    }
    return token
  }

  function actionIsCurrent(accountId: string, token: ReturnType<typeof client.captureSelectionToken>): boolean {
    return client.selectionTokenIsCurrent(token) && currentContext.current.ownerScopeKey === scope.cacheKey &&
      (!currentContext.current.desiredAccountId || currentContext.current.desiredAccountId === accountId)
  }

  const returnFromDetail = useCallback(() => {
    setComposer(null)
    setDraft(null)
    if (returnTo || route.returnTo) onReturn()
    else navigate(createShellRoute('apps', { view: 'list', placement: placementFor(account?.id) }))
  }, [account?.id, navigate, onReturn, returnTo, route])

  const loadAccounts = useCallback(async () => {
    const epoch = ++loadEpoch.current
    const requestIsCurrent = () => epoch === loadEpoch.current && currentContext.current.ownerScopeKey === scope.cacheKey &&
      currentContext.current.desiredAccountId === desiredAccountId
    setLoading(true); setAccountError(''); setMailError('')
    if (loadedScopeRef.current !== scope.cacheKey) {
      client.clear(); setAccounts([]); setAccount(null); setSnapshot(null); setDrafts([]); setDraft(null)
      setBusy(false); setReadBusy(false)
      loadedAccountRef.current = null
    }
    try {
      const rows = await client.readMirrorAccounts()
      if (!requestIsCurrent()) return
      setAccounts(rows)
      const chosen = desiredAccountId ? rows.find(row => row.id === desiredAccountId) ?? null : rows[0] ?? null
      if (desiredAccountId && !chosen) {
        client.clear(); setAccount(null); setSnapshot(null); setDrafts([])
        setAccountError('The selected native mailbox account is unavailable for this owner. Choose a listed account before continuing.')
        return
      }
      if (!chosen) {
        client.clear(); setAccount(null); setSnapshot(null); setDrafts([]); loadedScopeRef.current = scope.cacheKey; setLoadedScopeKey(scope.cacheKey)
        return
      }
      if (loadedAccountRef.current !== chosen.id) {
        client.clear(); setSnapshot(null); setDrafts([]); setDraft(null)
        setComposer(null); setTo(''); setSubject(''); setBody(''); setArtifactIds(''); setNotice('')
        setArtifactChoices([]); setArtifactError(''); setArtifactLoading(false)
        setBusy(false); setReadBusy(false)
        loadedAccountRef.current = chosen.id
      }
      client.selectConnection(chosen.id, 'mail-mirror')
      setAccount(chosen)
      const [messagesResult, draftRows] = await Promise.allSettled([
        client.readMirrorMessages(chosen), client.readOutboundDrafts(),
      ])
      if (!requestIsCurrent()) return
      if (messagesResult.status === 'fulfilled') setSnapshot(messagesResult.value)
      else { setMailError(messagesResult.reason instanceof Error ? messagesResult.reason.message : 'Mailbox refresh failed'); setSnapshot(null) }
      if (draftRows.status === 'fulfilled') setDrafts(draftRows.value)
      else setMailError(current => current || (draftRows.reason instanceof Error ? draftRows.reason.message : 'Draft refresh failed'))
      loadedScopeRef.current = scope.cacheKey; setLoadedScopeKey(scope.cacheKey)
    } catch (error) {
      if (requestIsCurrent()) {
        const denied = error as { status?: number; authRequired?: boolean }
        if (denied.authRequired === true || denied.status === 401 || denied.status === 403) {
          client.clear(); setAccounts([]); setAccount(null); setSnapshot(null); setDrafts([]); setDraft(null)
          setComposer(null); setTo(''); setSubject(''); setBody(''); setArtifactIds(''); setNotice('')
          setArtifactChoices([]); setArtifactError(''); setArtifactLoading(false); setBusy(false); setReadBusy(false)
          loadedAccountRef.current = null; loadedScopeRef.current = ''
        }
        setAccountError(error instanceof Error ? error.message : 'Could not load mailbox accounts')
      }
    } finally { if (requestIsCurrent()) setLoading(false) }
  }, [client, desiredAccountId, scope.cacheKey])

  useEffect(() => { void loadAccounts() }, [loadAccounts])
  useEffect(() => () => client.dispose(), [client])
  useEffect(() => {
    setComposer(null); setTo(''); setSubject(''); setBody(''); setArtifactIds('')
    setDraft(null); setNotice(''); setArtifactChoices([]); setArtifactError(''); setArtifactLoading(false)
  }, [scope.cacheKey, desiredAccountId])
  useEffect(() => {
    if (!composer || !account || !accountMatchesRoute) return
    let active = true
    let token: ReturnType<typeof client.captureSelectionToken>
    try { token = captureAction(account.id) }
    catch (error) { setArtifactError(error instanceof Error ? error.message : 'Selected mailbox changed'); return }
    setArtifactLoading(true); setArtifactError('')
    void fetch('/api/artifacts', { credentials: 'same-origin', headers: { 'X-Gideon-API-Version': '1', 'X-Session-Key': 'dashboard:ui' } })
      .then(async response => {
        const payload: unknown = await response.json()
        if (!response.ok) throw new Error('Gideon artifacts are unavailable for this owner. Retry before attaching files.')
        const rows = (payload as { artifacts?: unknown }).artifacts
        if (!Array.isArray(rows)) throw new Error('Gideon returned an invalid artifact list.')
        return rows.flatMap((row): ArtifactChoice[] => {
          if (!row || typeof row !== 'object') return []
          const value = row as Record<string, unknown>
          return typeof value.slug === 'string' && typeof value.name === 'string' && Number.isInteger(value.version) && Number(value.version) > 0
            ? [{ slug: value.slug, name: value.name, version: Number(value.version) }] : []
        })
      })
      .then(rows => {
        if (active && actionIsCurrent(account.id, token)) setArtifactChoices(rows)
      })
      .catch(error => {
        if (active && actionIsCurrent(account.id, token)) setArtifactError(error instanceof Error ? error.message : 'Gideon artifacts could not be loaded.')
      })
      .finally(() => { if (active && actionIsCurrent(account.id, token)) setArtifactLoading(false) })
    return () => { active = false }
  }, [account?.id, accountMatchesRoute, client, composer, desiredAccountId, scope.cacheKey])

  const listItems = useMemo(() => {
    const inTab = tab === 'unread' ? unreadMailItems(snapshot?.value ?? []) : snapshot?.value ?? []
    return filterMailItems(inTab, query)
  }, [query, snapshot, tab])
  const selectedDraft = route.record?.kind === 'mail-draft'
    ? drafts.find(item => mailDraftRouteId(item) === route.record?.id)
    : undefined
  const routeRecordId = route.record?.id
  const selectedMessage = route.record?.kind === 'mail-message'
    ? snapshot?.value.find(item => providerItemKey(item.identity) === route.record?.id)
    : undefined
  const selectedThread = selectedMessage
    ? selectedMessage.value.thread_id
      ? (snapshot?.value ?? []).filter(item => item.value.thread_id === selectedMessage.value.thread_id)
      : [selectedMessage]
    : []
  const returnContext = returnTo ?? route.returnTo
  const detail = selectedMessage || selectedDraft

  useEffect(() => {
    if (!detail && route.view === 'list') listFocusRef.current?.focus()
  }, [detail, route])

  useEffect(() => {
    if (desiredAccountId && account && account.id !== desiredAccountId) {
      client.clear()
      setAccount(null); setSnapshot(null); setDrafts([]); setDraft(null); setLoading(true)
    }
  }, [account, client, desiredAccountId])

  function goToMessage(item: CommunicationItem<MirrorMessage>, element: HTMLButtonElement) {
    listFocusRef.current = element
    navigate(createShellRoute('apps', { view: 'detail', record: { kind: 'mail-message', id: providerItemKey(item.identity) }, placement: placementFor(account?.id), returnTo: returnContext }))
  }
  function goToDraft(item: OutboundDraftItem, element: HTMLButtonElement) {
    listFocusRef.current = element
    navigate(createShellRoute('apps', { view: 'detail', record: { kind: 'mail-draft', id: mailDraftRouteId(item) }, placement: placementFor(account?.id), returnTo: returnContext }))
  }
  function startCompose(source?: MirrorMessage) {
    setDraft(null); setComposer({ source }); setTo(source ? source.sender.join(', ') : '')
    setSubject(source ? (source.subject.toLowerCase().startsWith('re:') ? source.subject : `Re: ${source.subject}`) : '')
    setBody(''); setArtifactIds(''); setNotice('')
    if (route.record) navigate(createShellRoute('apps', { view: 'list', placement: placementFor(account?.id), returnTo: returnContext }))
  }
  function editDraft(item: OutboundDraftItem) {
    setDraft(null)
    setComposer({ editDraft: item })
    setTo(item.value.to.join(', ')); setSubject(item.value.subject); setBody(item.value.body)
    setArtifactIds(item.value.attachments.map(file => file.artifact_id).join('\n'))
    setNotice('Editing this draft. Saving a change clears its prior approval and requires a fresh review.')
    navigate(createShellRoute('apps', { view: 'list', placement: placementFor(account?.id), returnTo: returnContext }))
  }
  async function saveDraft(review: boolean) {
    if (!account) return
    const recipients = to.split(/[,;\n]/).map(value => value.trim()).filter(Boolean)
    if (!recipients.length || !body.trim()) { setNotice('Add at least one recipient and a message before saving.'); return }
    let token: ReturnType<typeof client.captureSelectionToken>
    try { token = captureAction(account.id) }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Mailbox selection changed'); return }
    setBusy(true); setNotice('')
    try {
      const attachmentRefs = artifactIds.split(/[\n,;]/).map(value => value.trim()).filter(Boolean).map(artifact_id => {
        const selected = artifactChoices.find(item => item.slug === artifact_id)
        if (!selected) throw new Error('Choose each attachment from the current Gideon artifact list before saving.')
        const previousVersion = composer?.editDraft?.value.attachments.find(item => item.artifact_id === artifact_id)?.version
        return { artifact_id, version: previousVersion ?? selected.version }
      })
      const sourceMessageId = composer?.source?.external_id ?? composer?.editDraft?.value.source_message_id ?? null
      const created = composer?.editDraft
        ? await client.editOutboundDraft(composer.editDraft, { account_id: account.id, revision: composer.editDraft.value.revision,
          to: recipients, subject, body, source_message_id: sourceMessageId ?? '', attachments: attachmentRefs } satisfies OutboundDraftEditInput)
        : await client.createOutboundDraft({ account_id: account.id, request_key: crypto.randomUUID(), to: recipients,
          subject, body, source_message_id: sourceMessageId ?? undefined, attachments: attachmentRefs } satisfies OutboundDraftInput)
      if (!actionIsCurrent(account.id, token)) return
      setDraft(created)
      setDrafts(current => [created, ...current.filter(item => item.value.id !== created.value.id)])
      setComposer(null)
      setNotice(review ? 'Draft saved. Review its exact account, recipients, content, attachments and revision before approval.' : composer?.editDraft ? `Draft updated to revision ${created.value.revision}; the previous approval was cleared.` : 'Draft saved to this account.')
      if (review) navigate(createShellRoute('apps', { view: 'detail', record: { kind: 'mail-draft', id: mailDraftRouteId(created) }, placement: placementFor(account.id), returnTo: returnContext }))
      else setTab('drafts')
    } catch (error) { if (actionIsCurrent(account.id, token)) setNotice(error instanceof Error ? error.message : 'Draft could not be saved') }
    finally { if (actionIsCurrent(account.id, token)) setBusy(false) }
  }
  async function approve(item: OutboundDraftItem) {
    let token: ReturnType<typeof client.captureSelectionToken>
    try { token = captureAction(item.identity.accountId) }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Mailbox selection changed'); return }
    setBusy(true); setNotice('')
    try {
      const result = await client.approveOutboundDraft(item)
      if (!actionIsCurrent(item.identity.accountId, token)) return
      setDraft(result); setDrafts(rows => rows.map(row => row.value.id === result.value.id ? result : row))
      setNotice(`Approved the exact content for ${account?.name ?? 'this mailbox'}, revision ${result.value.revision}. Review the same recipient and content before sending.`)
    } catch (error) { if (actionIsCurrent(item.identity.accountId, token)) setNotice(error instanceof Error ? error.message : 'Approval could not be confirmed') }
    finally { if (actionIsCurrent(item.identity.accountId, token)) setBusy(false) }
  }
  async function send(item: OutboundDraftItem) {
    if (item.value.state !== 'approved' || !window.confirm(`Send this approved email once from ${account?.name} to ${item.value.to.join(', ')}?`)) return
    let token: ReturnType<typeof client.captureSelectionToken>
    try { token = captureAction(item.identity.accountId) }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Mailbox selection changed'); return }
    setBusy(true); setNotice('Sending through Gideon’s governed outbound path…')
    try {
      const result = await client.sendOutboundDraft(item)
      if (!actionIsCurrent(item.identity.accountId, token)) return
      setDraft(result); setDrafts(rows => rows.map(row => row.value.id === result.value.id ? result : row))
      setNotice(`Send result: ${result.value.state}; delivery ${result.value.delivery}.`)
    } catch (error) {
      if (actionIsCurrent(item.identity.accountId, token)) setNotice(`${error instanceof Error ? error.message : 'Send result unavailable'} Reconcile this same draft before retrying.`)
    } finally { if (actionIsCurrent(item.identity.accountId, token)) setBusy(false) }
  }
  async function reconcile(item: OutboundDraftItem) {
    let token: ReturnType<typeof client.captureSelectionToken>
    try { token = captureAction(item.identity.accountId) }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Mailbox selection changed'); return }
    setBusy(true)
    try {
      const result = await client.correlateOutboundDraft(item.value.id, item.value.account_id)
      if (!actionIsCurrent(item.identity.accountId, token)) return
      setDraft(result); setDrafts(rows => rows.map(row => row.value.id === result.value.id ? result : row))
      setNotice(`Reconciled send result: ${result.value.state}; delivery ${result.value.delivery}.`)
    } catch (error) { if (actionIsCurrent(item.identity.accountId, token)) setNotice(error instanceof Error ? error.message : 'Reconciliation failed') }
    finally { if (actionIsCurrent(item.identity.accountId, token)) setBusy(false) }
  }
  async function setMessageReadState(item: CommunicationItem<MirrorMessage>, isRead: boolean) {
    let token: ReturnType<typeof client.captureSelectionToken>
    try { token = captureAction(item.identity.accountId) }
    catch (error) { setNotice(error instanceof Error ? error.message : 'Mailbox selection changed'); return }
    setReadBusy(true)
    try {
      const updated = await client.setMirrorMessageReadState(item.identity.accountId, item.value.external_id, isRead)
      if (!actionIsCurrent(item.identity.accountId, token) || !updated) return
      setSnapshot(current => current ? { ...current, value: current.value.map(row => providerItemKey(row.identity) === providerItemKey(updated.identity) ? updated : row) } : current)
      setNotice(`Read state updated locally in Gideon. The mailbox provider was not changed.`)
    } catch (error) { if (actionIsCurrent(item.identity.accountId, token)) setNotice(error instanceof Error ? error.message : 'Read state could not be updated') }
    finally { if (actionIsCurrent(item.identity.accountId, token)) setReadBusy(false) }
  }

  const mode = typeof window !== 'undefined' && window.matchMedia('(max-width: 700px)').matches ? 'compact' : 'full'
  const themeVariables = { '--mail-card': palette.card, '--mail-text': palette.text, '--mail-muted': palette.muted,
    '--mail-line': palette.line, '--mail-green': palette.green, '--mail-danger': palette.danger } as React.CSSProperties
  return <WorkspaceFrame route={route} mode={mode} title={detail ? 'Mail detail' : 'Mail'} onBack={detail ? returnFromDetail : undefined}>
    <div style={{ ...themeVariables, display: 'grid', gap: 16, padding: 'clamp(14px, 3vw, 28px)', maxWidth: 1040, margin: '0 auto', width: '100%', boxSizing: 'border-box' }}>
      {accountError && <section role="alert" style={panel}><h2>Mailbox account unavailable</h2><p>{accountError}</p>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><button type="button" onClick={() => void loadAccounts()} style={primary}>Retry accounts</button>
          {accountMatchesScope && accounts.length > 0 && <label>Choose mailbox <select aria-label="Choose mailbox account" value="" onChange={event => navigate(createShellRoute('apps', { view: 'list', placement: placementFor(event.currentTarget.value) }))} style={field}>
            <option value="" disabled>Select a registered account</option>{accounts.map(row => <option key={row.id} value={row.id}>{row.name} · {row.owner_email}</option>)}
          </select></label>}</div></section>}
      {loading && <p role="status">Loading connected mailbox accounts…</p>}
      {!loading && !accountError && accountMatchesScope && accounts.length === 0 && <section style={panel}><h2>No mailbox accounts</h2><p style={subtle}>There are no connected mailbox accounts for this Gideon owner. Live mailbox access is unavailable here.</p><button type="button" onClick={onReturn} style={button}>Return to workspace</button></section>}
      {account && accountMatchesRoute && accountMatchesScope && <>
        {!detail && <>
          <section style={{ ...panel, display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 14, alignItems: 'end' }}>
            <label style={{ display: 'grid', gap: 7, color: 'var(--mail-text)', fontWeight: 600 }}>Mailbox account
              <select aria-label="Mailbox account" value={account.id} onChange={event => navigate(createShellRoute('apps', { view: 'list', placement: placementFor(event.currentTarget.value) }))} style={field}>
                {accounts.map(row => <option key={row.id} value={row.id}>{row.name} · {row.owner_email} · {row.kind.toUpperCase()}</option>)}
              </select>
            </label>
            <div><strong>{account.name}</strong><p style={{ ...subtle, margin: '4px 0' }}>{account.owner_email} · {account.kind === 'imap' ? (['expired', 'revoked', 'unavailable', 'importing', 'failed', 'error', 'connected', 'read_only', 'read-only'].includes(account.sync?.state ?? '') ? connectionReadiness(account.sync?.state) : mirrorReadiness(account)) : mirrorReadiness(account)}</p>
              {account.sync?.state === 'failed' && <p role="alert" style={{ color: 'var(--mail-danger)' }}>Mailbox sync failed. Retry refresh or reconnect this account in Gideon before using outbound actions.</p>}
              {(account.sync?.state === 'expired' || account.sync?.state === 'revoked') && <p role="alert" style={{ color: 'var(--mail-danger)' }}>This account connection has expired. Reconnect it in Gideon before continuing.</p>}
              {account.sync?.state === 'unavailable' && <p role="status">This account is unavailable. Retry after its native connection is restored.</p>}</div>
          </section>
          <nav aria-label="Mailbox views" style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {(['all', 'unread', 'drafts'] as const).map(value => <button key={value} type="button" onClick={() => setTab(value)} aria-pressed={tab === value} style={tab === value ? primary : button}>
              {value === 'all' ? 'All messages' : value === 'unread' ? `Unread (${(snapshot?.value ?? []).filter(item => !item.value.is_read).length})` : `Drafts (${drafts.length})`}
            </button>)}
            <button type="button" onClick={() => startCompose()} style={{ ...primary, marginInlineStart: 'auto' }}>Compose</button>
          </nav>
          {tab === 'unread' && <p role="status">Unread is tracked locally in Gideon. It does not change provider read state.</p>}
          {snapshot?.freshness === 'stale' && <p role="status">Showing the last known mailbox snapshot. Refresh failed; these records are stale.</p>}
          {mailError && <section role="alert" style={panel}><p>{mailError}</p><button type="button" style={button} onClick={() => void loadAccounts()}>Retry mailbox</button></section>}
          <label style={{ display: 'grid', gap: 7 }}>Search mail
            <input ref={searchRef} aria-label="Search mail" type="search" value={query} onChange={event => setQuery(event.currentTarget.value)} placeholder="Sender, recipient, subject or message" style={field} />
          </label>
          {composer && <section aria-labelledby="compose-heading" style={{ ...panel, display: 'grid', gap: 12 }}>
            <h2 id="compose-heading">{composer.editDraft ? 'Edit draft' : composer.source ? 'Reply draft' : 'New email draft'}</h2>
            <p style={subtle}>Saved to {account.name}. Review the exact recipient, message, and any selected Gideon artifacts before approval.</p>
            {composer.source && <p style={subtle}>Replying to {composer.source.sender.join(', ')} · {composer.source.subject || '(No subject)'}</p>}
            <label>To<input value={to} onChange={event => setTo(event.currentTarget.value)} autoComplete="email" style={field} /></label>
            <label>Subject<input value={subject} onChange={event => setSubject(event.currentTarget.value)} style={field} /></label>
            <label>Message<textarea value={body} onChange={event => setBody(event.currentTarget.value)} rows={8} style={{ ...field, resize: 'vertical' }} /></label>
            <label style={{ display: 'grid', gap: 7 }}>Attachments from Gideon artifacts
              <select aria-label="Gideon artifact attachments" multiple value={artifactIds.split(/[\n,;]/).map(value => value.trim()).filter(Boolean)}
                onChange={event => setArtifactIds(Array.from(event.currentTarget.selectedOptions, option => option.value).join('\n'))} style={{ ...field, minHeight: 100 }}>
                {artifactChoices.map(item => <option key={item.slug} value={item.slug}>{item.name} · v{item.version}</option>)}
              </select>
            </label>
            {artifactLoading && <p role="status">Loading available Gideon artifacts…</p>}
            {artifactError && <p role="alert">{artifactError}</p>}
            {!artifactLoading && !artifactError && artifactChoices.length === 0 && <p style={subtle}>No existing Gideon artifacts are available to attach.</p>}
            <div style={{ display: 'flex', gap: 9, flexWrap: 'wrap' }}>
              <button disabled={busy} type="button" style={button} onClick={() => setComposer(null)}>Cancel</button>
              <button disabled={busy} type="button" style={button} onClick={() => void saveDraft(false)}>{busy ? 'Saving…' : 'Save draft'}</button>
              <button disabled={busy} type="button" style={primary} onClick={() => void saveDraft(true)}>{busy ? 'Saving…' : 'Save and review'}</button>
            </div>
          </section>}
          {notice && <p role="status" aria-live="polite">{notice}</p>}
          {draft && <section style={panel} aria-label="Outbound draft review">
            <p style={subtle}>{account.name} · revision {draft.value.revision}</p>
            <h2>{draft.value.subject || '(No subject)'}</h2><p><strong>From:</strong> {draft.value.sender}</p>
            <p><strong>To:</strong> {draft.value.to.join(', ')}</p><p style={{ whiteSpace: 'pre-wrap' }}>{draft.value.body}</p>
            <ul>{draft.value.attachments.map(file => <li key={`${file.artifact_id}:${file.version}`}>{file.name} · {file.size} bytes</li>)}</ul>
            {draft.value.state === 'draft' && <button type="button" disabled={busy} style={primary} onClick={() => void approve(draft)}>Approve this exact draft</button>}
            {draft.value.state === 'approved' && <button type="button" disabled={busy} style={primary} onClick={() => void send(draft)}>Send once…</button>}
            {['uncertain', 'sending', 'failed'].includes(draft.value.state) && <button type="button" disabled={busy} style={button} onClick={() => void reconcile(draft)}>Reconcile existing result</button>}
          </section>}
          {tab === 'drafts' ? <section style={{ display: 'grid', gap: 10 }} aria-label="Outbound drafts">
            {drafts.length === 0 ? <p style={subtle}>This account has no outbound drafts.</p> : drafts.map(item => <button ref={element => { if (mailDraftRouteId(item) === routeRecordId) listFocusRef.current = element }} key={providerItemKey(item.identity)} onClick={event => goToDraft(item, event.currentTarget)} type="button" style={{ ...panel, textAlign: 'left', cursor: 'pointer' }}>
              <strong>{item.value.subject || '(No subject)'}</strong><span style={{ ...subtle, marginInlineStart: 8 }}>{item.value.state} · {item.value.to.join(', ')}</span><p style={{ marginBottom: 0 }}>{item.value.body.slice(0, 170)}</p>
            </button>)}
          </section> : <section style={{ display: 'grid', gap: 10 }} aria-label="Messages">
            {snapshot && listItems.length === 0 && <p style={subtle}>{snapshot.value.length === 0 ? 'This mailbox has no imported messages.' : tab === 'unread' && !query.trim() ? 'There are no unread messages in this Gideon mailbox view.' : 'No messages match this search. Clear the search to see the mailbox.'}</p>}
            {snapshot && listItems.map(item => <button ref={element => { if (providerItemKey(item.identity) === routeRecordId) listFocusRef.current = element }} key={providerItemKey(item.identity)} onClick={event => goToMessage(item, event.currentTarget)} type="button" style={{ ...panel, textAlign: 'left', cursor: 'pointer', display: 'grid', gap: 7 }}>
              <span style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}><strong>{item.value.sender.join(', ') || 'Sender unavailable'}</strong><span style={subtle}>{item.value.occurred_at ?? 'Date unavailable'}</span></span>
              <strong>{item.value.subject || '(No subject)'}</strong><span style={{ ...subtle, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.value.body}</span>
              <span style={subtle}>{item.value.recipients.length ? `To ${item.value.recipients.join(', ')}` : 'Recipients unavailable'} · {item.value.attachments.length} attachments · {item.value.direction} · {item.value.is_read ? 'Read in Gideon' : 'Unread in Gideon'} · local read state · {item.freshness}</span>
            </button>)}
            {!snapshot && !mailError && <p role="status">Mailbox messages have not loaded.</p>}
          </section>}
        </>}
        {detail && account && <MailDetail account={account} item={selectedDraft ?? selectedMessage!} thread={selectedThread} onBack={returnFromDetail}
          onReply={startCompose} onSetReadState={(item, isRead) => void setMessageReadState(item, isRead)} readBusy={readBusy}
          onEditDraft={editDraft} onReviewDraft={item => { setDraft(item); navigate(createShellRoute('apps', { view: 'list', placement: placementFor(account.id), returnTo: returnContext })) }} />}
        {!detail && route.view === 'detail' && !loading && <section role="alert" style={panel}>
          <h2>Message or draft unavailable</h2><p style={subtle}>This mailbox did not return the requested provider item for the selected account.</p>
          <button type="button" onClick={() => void loadAccounts()} style={button}>Refresh selected account</button>
        </section>}
      </>}
    </div>
  </WorkspaceFrame>
}

export default MailWorkspace
