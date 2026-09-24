import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ArrowDown, ArrowLeft, ArrowUp, AtSign, Check, Download, LoaderCircle, MessageSquare, Plus, Search, Settings2, ShieldCheck, Square, Users, X } from 'lucide-react'
import type { RouteProps } from '../../app/shell/useQueryState'
import { api, type PendingApproval, type SavedAgent } from '../../shared/data/api'
import { Markdown } from '../../shared/ui/Markdown'
import { FocusScope } from '../../shared/ui/focusNavigation'
import { RoomEditor } from './RoomEditor'
import { roomsApi, type Room, type RoomMember, type RoomMessage, type RoomsIndex, type RoomTurn } from './roomsApi'
import { changedDraft, draftKey, filterRooms, insertMention, isActiveTurn, mentionAt, mentionLabel, readDraft, roomApprovals, writeDraft } from './roomsState'
import { roomsAccountScope, t } from './roomsText'
import './rooms.css'

function errorMessage(cause: unknown): string {
  return cause instanceof Error ? cause.message : t('Something went wrong. Please try again.')
}

function Avatar({ name, index = 0 }: { name: string; index?: number }) {
  return <span className={`rooms-avatar rooms-avatar-${index % 4}`} aria-hidden="true">{name.slice(0, 2).toLocaleUpperCase()}</span>
}

function MessageTime({ message }: { message: RoomMessage }) {
  const raw = message.created_at || message.ts
  if (!raw) return null
  const date = new Date(typeof raw === 'number' && raw < 1e12 ? raw * 1000 : raw)
  if (Number.isNaN(date.getTime())) return null
  return <time dateTime={date.toISOString()} title={date.toLocaleString()}>{date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</time>
}

function mergeMessages(before: RoomMessage[], after: RoomMessage[]): RoomMessage[] {
  const rows = new Map(before.map(message => [message.id, message]))
  for (const message of after) rows.set(message.id, message)
  return [...rows.values()]
}

function RoomConversation({ id, agents, maxMembers, roundBudget, onBack, onChanged, onDeleted }: {
  id: string
  agents: SavedAgent[]
  maxMembers: number
  roundBudget: number
  onBack: () => void
  onChanged: (room: Room) => void
  onDeleted: () => void
}) {
  const [room, setRoom] = useState<Room | null>(null)
  const [messages, setMessages] = useState<RoomMessage[]>([])
  const [turn, setTurn] = useState<RoomTurn | null>(null)
  const [approvals, setApprovals] = useState<PendingApproval[]>([])
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [loading, setLoading] = useState(true)
  const [settings, setSettings] = useState(false)
  const [overlaySettings, setOverlaySettings] = useState(() => window.matchMedia('(max-width: 1200px)').matches)
  const [exporting, setExporting] = useState(false)
  const [sending, setSending] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [deciding, setDeciding] = useState<string | null>(null)
  const [before, setBefore] = useState<string | null>(null)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const key = draftKey(roomsAccountScope(), id)
  const [draft, setDraft] = useState(() => readDraft(key))
  const [cursor, setCursor] = useState(draft.text.length)
  const [mentionIndex, setMentionIndex] = useState(0)
  const [hideMentions, setHideMentions] = useState(false)
  const [atEnd, setAtEnd] = useState(true)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const settingsButton = useRef<HTMLButtonElement>(null)
  const settingsPane = useRef<HTMLElement>(null)
  const transcript = useRef<HTMLDivElement>(null)
  const following = useRef(true)
  const alive = useRef(true)
  const loadedOlder = useRef(false)
  const requestSequence = useRef(0)
  const active = isActiveTurn(turn)
  const changedRef = useRef(onChanged)
  changedRef.current = onChanged
  const closeSettings = useCallback(() => {
    setSettings(false)
    requestAnimationFrame(() => settingsButton.current?.focus({ preventScroll: true }))
  }, [])
  useEffect(() => {
    const media = window.matchMedia('(max-width: 1200px)')
    const changed = () => setOverlaySettings(media.matches)
    media.addEventListener('change', changed)
    return () => media.removeEventListener('change', changed)
  }, [])
  useEffect(() => {
    if (settings && overlaySettings && settingsPane.current) return new FocusScope(settingsButton.current).attach(settingsPane.current)
  }, [settings, overlaySettings])
  const refresh = useCallback(async () => {
    const sequence = ++requestSequence.current
    try {
      const [detail, journal, state, pending] = await Promise.all([roomsApi.detail(id), roomsApi.transcript(id), roomsApi.turn(id), api.approvals()])
      if (!alive.current || sequence !== requestSequence.current) return
      setRoom(detail.room)
      changedRef.current(detail.room)
      setMessages(current => mergeMessages(current, journal.messages))
      if (!loadedOlder.current) setBefore(journal.has_more ? journal.before : null)
      setTurn(state.turn)
      setApprovals(roomApprovals(pending, id))
      setError('')
    } catch (cause) {
      if (alive.current && sequence === requestSequence.current) setError(errorMessage(cause))
    } finally { if (alive.current && sequence === requestSequence.current) setLoading(false) }
  }, [id])
  const activeRef = useRef(active)
  activeRef.current = active
  useEffect(() => {
    alive.current = true
    let timeout: ReturnType<typeof setTimeout>
    const poll = async () => {
      await refresh()
      if (alive.current) timeout = setTimeout(() => void poll(), document.hidden ? 10000 : activeRef.current ? 1200 : 5000)
    }
    const visible = () => { if (!document.hidden) void refresh() }
    void poll()
    document.addEventListener('visibilitychange', visible)
    return () => { alive.current = false; clearTimeout(timeout); document.removeEventListener('visibilitychange', visible) }
  }, [refresh])
  useLayoutEffect(() => {
    const node = transcript.current
    if (node && following.current) node.scrollTop = node.scrollHeight
  }, [messages, turn?.text, approvals.length])
  useLayoutEffect(() => {
    if (textarea.current) { textarea.current.style.height = 'auto'; textarea.current.style.height = `${Math.min(textarea.current.scrollHeight, 180)}px` }
  }, [draft.text])
  const changeDraft = (text: string) => {
    setDraft(current => { const next = changedDraft(current, text); writeDraft(key, next); return next })
    setHideMentions(false)
    setMentionIndex(0)
  }
  const mention = hideMentions ? null : mentionAt(draft.text, cursor)
  const mentionOptions = room && mention ? [
    { value: 'everyone', name: t('Everyone'), detail: t('All listening members') },
    ...room.members.filter(member => member.listen_policy !== 'none').map(member => ({ value: mentionLabel(member, room.members), name: member.name, detail: member.role || member.agent })),
  ].filter(item => `${item.name} ${item.value}`.toLocaleLowerCase().includes(mention.query)) : []
  const chooseMention = (value: string) => {
    const next = insertMention(draft.text, cursor, value)
    changeDraft(next.text)
    setCursor(next.cursor)
    setHideMentions(true)
    requestAnimationFrame(() => { textarea.current?.focus(); textarea.current?.setSelectionRange(next.cursor, next.cursor) })
  }
  const send = async () => {
    if (!draft.text.trim() || sending || active || !room || error) return
    const submitted = draft
    setSending(true)
    setActionError('')
    following.current = true
    setAtEnd(true)
    try {
      const result = await roomsApi.send(id, submitted.text.trim(), submitted.requestId)
      if (!alive.current) return
      setTurn(result.turn)
      setDraft(current => {
        if (current.requestId !== submitted.requestId) return current
        const empty = changedDraft(current, '')
        writeDraft(key, empty)
        return empty
      })
      await refresh()
    } catch (cause) { if (alive.current) setActionError(errorMessage(cause)) } finally { if (alive.current) { setSending(false); textarea.current?.focus() } }
  }
  const action = async (operation: () => Promise<unknown>) => {
    setActionError('')
    try { await operation(); await refresh() } catch (cause) { if (alive.current) setActionError(errorMessage(cause)) }
  }
  const earlier = async () => {
    if (!before || loadingOlder) return
    setLoadingOlder(true)
    const node = transcript.current
    const height = node?.scrollHeight ?? 0
    const position = node?.scrollTop ?? 0
    following.current = false
    try {
      const result = await roomsApi.transcript(id, before)
      if (!alive.current) return
      loadedOlder.current = true
      setMessages(current => mergeMessages(result.messages, current))
      setBefore(result.has_more ? result.before : null)
      requestAnimationFrame(() => { if (node) node.scrollTop = position + node.scrollHeight - height })
    } catch (cause) { if (alive.current) setActionError(errorMessage(cause)) } finally { if (alive.current) setLoadingOlder(false) }
  }
  const activeMember = room?.members.find(member => member.id === turn?.member_id)
  const activeName = activeMember?.name || t('Members')
  if (loading) return <main className="rooms-conversation rooms-center" aria-busy="true"><LoaderCircle className="rooms-spin" size={22} /><p>{t('Opening room…')}</p></main>
  if (!room) return <main className="rooms-conversation rooms-center"><MessageSquare size={30} /><h2>{t('Could not open this room')}</h2><p role="alert">{error}</p><div className="rooms-actions"><button className="rooms-button" onClick={() => void refresh()}>{t('Retry')}</button><button className="rooms-button rooms-quiet" onClick={onBack}>{t('Back to rooms')}</button></div></main>
  return <div className={`rooms-conversation-layout ${settings ? 'settings-open' : ''}`}>
    <main className="rooms-conversation" aria-label={room.name} inert={settings && overlaySettings}>
      <header className="rooms-conversation-header"><button className="rooms-icon rooms-back" aria-label={t('Back to rooms')} onClick={onBack}><ArrowLeft size={19} /></button><div className="rooms-room-title"><h2 dir="auto">{room.name}</h2><p>{t('{p0} members', [room.members.length])}<span aria-hidden="true"> · </span>{active ? t('Conversation in progress') : t('Shared conversation')}</p></div><button ref={settingsButton} className="rooms-icon" aria-label={t('Room settings')} title={t('Room settings')} aria-expanded={settings} onClick={() => setSettings(value => !value)}><Settings2 size={19} /></button></header>
      <div className="rooms-member-strip" aria-label={t('Room members')}>{room.members.map((member, index) => <button className={`rooms-member-chip ${active && activeMember?.id === member.id ? 'is-working' : ''} ${member.listen_policy === 'none' ? 'is-paused' : ''}`} key={member.id} title={member.role || member.agent} onClick={() => { const prefix = draft.text && !/\s$/.test(draft.text) ? ' ' : ''; const text = `${draft.text}${prefix}@${mentionLabel(member, room.members)} `; changeDraft(text); setCursor(text.length); textarea.current?.focus() }}><Avatar name={member.name} index={index} /><span dir="auto">{member.name}</span>{active && activeMember?.id === member.id && <LoaderCircle size={12} className="rooms-spin" />}{member.listen_policy === 'none' && <span className="rooms-hint">{t('Paused')}</span>}</button>)}</div>
      {error && <div className="rooms-error rooms-inline-error" role="alert"><span>{error}</span><button className="rooms-button rooms-quiet" onClick={() => void refresh()}>{t('Retry')}</button></div>}
      <div ref={transcript} className="rooms-transcript" role="log" aria-label={t('Shared conversation')} aria-live="off" tabIndex={0} onScroll={() => { const node = transcript.current; if (node) { const near = node.scrollHeight - node.scrollTop - node.clientHeight < 100; following.current = near; setAtEnd(near) } }}>
        {before && <button className="rooms-button rooms-load-earlier" disabled={loadingOlder} onClick={() => void earlier()}>{loadingOlder ? t('Loading…') : t('Load earlier messages')}</button>}
        {!messages.length && !active && <div className="rooms-conversation-intro"><div className="rooms-intro-glyph"><Users size={29} /></div><h3>{t('Bring your agents into the conversation')}</h3><p>{t('Share an idea, ask for different perspectives, or work through a decision together. Each member can build on the replies before them.')}</p><div className="rooms-prompt-grid">{[t('Introduce yourselves and explain how you can help.'), t('Help me explore an idea from different perspectives.')].map(prompt => <button key={prompt} onClick={() => { changeDraft(prompt); textarea.current?.focus() }}>{prompt}<ArrowUp size={15} /></button>)}</div></div>}
        {messages.map(message => {
          const index = room.members.findIndex(member => member.id === message.speaker)
          const member = room.members[index]
          const name = message.role === 'user' ? t('You') : message.role === 'system' ? t('Room update') : message.speaker_name || member?.name || t('Former member')
          return <article className={`rooms-message rooms-message-${message.role}`} key={message.id}><Avatar name={name} index={index < 0 ? 3 : index} /><div className="rooms-message-body"><header><strong dir="auto">{name}</strong><MessageTime message={message} /></header><div dir="auto" className="rooms-message-content"><Markdown>{message.content}</Markdown></div></div></article>
        })}
        {active && <article className="rooms-message rooms-message-live"><Avatar name={activeName} index={room.members.findIndex(member => member.id === activeMember?.id)} /><div className="rooms-message-body"><header><strong dir="auto">{activeName}</strong><span className="rooms-working"><LoaderCircle className="rooms-spin" size={12} />{approvals.length ? t('Awaiting approval') : t('Working…')}</span></header>{turn?.text ? <div dir="auto" className="rooms-message-content"><Markdown streaming>{turn.text}</Markdown></div> : <div className="rooms-thinking"><span /><span /><span /></div>}</div></article>}
        {turn?.status === 'failed' && <div className="rooms-error" role="alert">{turn.error || t('This turn could not finish. You can send a follow-up message.')}</div>}
        {turn?.status === 'cancelled' && <p className="rooms-turn-note">{t('Turn stopped. Completed replies are saved.')}</p>}
      </div>
      <div className="rooms-composer-area">
        {!atEnd && <button className="rooms-jump rooms-button" onClick={() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; following.current = true; setAtEnd(true) }}><ArrowDown size={14} />{t('Latest messages')}</button>}
        {approvals.map(approval => <section className="rooms-approval" key={approval.id} aria-label={t('Human approval required')}><div className="rooms-approval-heading"><ShieldCheck size={18} /><strong>{t('Your approval is needed')}</strong></div><p><bdi>{room.members.find(member => approval.session === `room:${id}:${member.id}`)?.name || t('A room member')}</bdi><span> · </span><bdi>{approval.tool}</bdi></p>{approval.tool_purpose && <p dir="auto">{approval.tool_purpose}</p>}{approval.tool_input != null && <details><summary>{t('Review action details')}</summary><pre>{typeof approval.tool_input === 'string' ? approval.tool_input : JSON.stringify(approval.tool_input, null, 2)}</pre></details>}<div className="rooms-actions"><button className="rooms-button rooms-primary" disabled={deciding !== null} onClick={() => { setDeciding(approval.id); void action(() => api.resolveApproval(approval.id, 'approve')).finally(() => setDeciding(null)) }}><Check size={15} />{t('Approve once')}</button><button className="rooms-button" disabled={deciding !== null} onClick={() => { setDeciding(approval.id); void action(() => api.resolveApproval(approval.id, 'reject')).finally(() => setDeciding(null)) }}><X size={15} />{t('Reject')}</button></div></section>)}
        {actionError && <div className="rooms-error" role="alert">{actionError}<span className="rooms-hint"> {draft.text ? t('Your draft is saved. Try sending again.') : ''}</span></div>}
        <form className="rooms-composer" onSubmit={event => { event.preventDefault(); void send() }}>
          {mentionOptions.length > 0 && <div className="rooms-mention-menu" role="listbox" id={`room-mentions-${id}`} aria-label={t('Mention a member')}>{mentionOptions.map((option, index) => <button type="button" role="option" id={`room-mention-${id}-${index}`} aria-selected={index === mentionIndex} className={index === mentionIndex ? 'selected' : ''} key={option.value} onMouseDown={event => event.preventDefault()} onClick={() => chooseMention(option.value)}><AtSign size={16} /><span><strong dir="auto">{option.name}</strong><small dir="auto">{option.detail}</small></span></button>)}</div>}
          <textarea ref={textarea} value={draft.text} rows={2} maxLength={100000} aria-label={t('Message this room')} placeholder={t('Message the room, or @mention a member…')} aria-controls={mentionOptions.length ? `room-mentions-${id}` : undefined} aria-activedescendant={mentionOptions.length ? `room-mention-${id}-${mentionIndex}` : undefined} onChange={event => { changeDraft(event.target.value); setCursor(event.target.selectionStart) }} onSelect={event => setCursor(event.currentTarget.selectionStart)} onKeyDown={event => {
            if (event.nativeEvent.isComposing) return
            if (mentionOptions.length) {
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); setMentionIndex(value => (value + (event.key === 'ArrowDown' ? 1 : mentionOptions.length - 1)) % mentionOptions.length); return }
              if (event.key === 'Escape') { event.preventDefault(); setHideMentions(true); return }
              if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); chooseMention(mentionOptions[Math.min(mentionIndex, mentionOptions.length - 1)].value); return }
            }
            if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send() }
          }} />
          <div className="rooms-composer-footer"><span className="rooms-hint">{active ? t('Members are taking turns. You can draft your next message.') : t('Enter to send · Shift + Enter for a new line')}</span>{active ? <button type="button" className="rooms-button" disabled={stopping} onClick={() => { setStopping(true); void action(() => roomsApi.cancel(id)).finally(() => setStopping(false)) }}><Square size={14} />{stopping ? t('Stopping…') : t('Stop')}</button> : <button className="rooms-send" type="submit" aria-label={t('Send message')} disabled={sending || !draft.text.trim() || Boolean(error)}>{sending ? <LoaderCircle size={19} className="rooms-spin" /> : <ArrowUp size={20} />}</button>}</div>
        </form>
        <p className="rooms-composer-note"><ShieldCheck size={12} />{t('Human approval stays in your hands.')}<span>{t('Up to {p0} replies per message', [roundBudget])}</span></p>
      </div>
      <span className="rooms-sr-only" role="status">{active ? `${activeName} · ${approvals.length ? t('Awaiting approval') : t('Working…')}` : t('Room ready')}</span>
    </main>
    {settings && <aside ref={settingsPane} className="rooms-settings-pane" role={overlaySettings ? 'dialog' : 'region'} aria-modal={overlaySettings || undefined} aria-label={t('Room settings')} onKeyDown={event => { if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeSettings() } }}><RoomEditor room={room} agents={agents} maxMembers={maxMembers} busy={active || sending} onClose={closeSettings} onSave={async (name, members) => { const result = await roomsApi.update(id, name, members); setRoom(result.room); onChanged(result.room); closeSettings() }} onDelete={async () => { await roomsApi.remove(id); onDeleted() }} /><div className="rooms-export"><span>{t('Save a copy of this conversation')}</span><button className="rooms-button rooms-quiet" disabled={exporting} onClick={() => { setExporting(true); void action(() => roomsApi.export(id, 'md')).finally(() => setExporting(false)) }}><Download size={15} />{t('Markdown')}</button><button className="rooms-button rooms-quiet" disabled={exporting} onClick={() => { setExporting(true); void action(() => roomsApi.export(id, 'json')).finally(() => setExporting(false)) }}>{t('JSON')}</button></div></aside>}
  </div>
}

export function RoomsSection({ sub, navigate, query, setQuery }: RouteProps) {
  const [index, setIndex] = useState<RoomsIndex | null>(null)
  const [agents, setAgents] = useState<SavedAgent[]>([])
  const [error, setError] = useState('')
  const [agentError, setAgentError] = useState('')
  const [loading, setLoading] = useState(true)
  const [enabling, setEnabling] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const alive = useRef(true)
  const refresh = useCallback(async () => {
    setRefreshing(true)
    const [rooms, roster] = await Promise.allSettled([roomsApi.list(), api.agents()])
    if (!alive.current) return
    if (rooms.status === 'fulfilled') { setIndex(rooms.value); setError('') } else setError(errorMessage(rooms.reason))
    if (roster.status === 'fulfilled') { setAgents(roster.value.agents); setAgentError('') } else setAgentError(errorMessage(roster.reason))
    setLoading(false)
    setRefreshing(false)
  }, [])
  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false } }, [refresh])
  const changed = useCallback((room: Room) => setIndex(current => current ? { ...current, rooms: current.rooms.some(item => item.id === room.id) ? current.rooms.map(item => item.id === room.id ? room : item) : [...current.rooms, room] } : current), [])
  const rooms = useMemo(() => filterRooms(index?.rooms ?? [], query.q || ''), [index?.rooms, query.q])
  const creating = sub === 'new'
  const selected = sub && !creating ? sub.split('/')[0] : ''
  const enabled = index?.enabled ?? false
  const goBack = () => navigate('rooms')
  return <div className={`rooms-workspace ${selected || creating ? 'has-detail' : ''} ${!enabled ? 'rooms-disabled' : ''}`}>
    <aside className="rooms-list" aria-label={t('Rooms')}>
      <header className="rooms-list-heading"><div><span className="rooms-eyebrow">{t('TOGETHER, BETTER')}</span><h1>{t('Rooms')}</h1></div><button className="rooms-icon rooms-create-button" aria-label={t('New room')} title={t('New room')} disabled={!enabled} onClick={() => navigate('rooms/new')}><Plus size={21} /></button></header>
      <p className="rooms-list-caption">{t('A shared space for your agents to think and work together.')}</p>
      <label className="rooms-search"><Search size={16} /><input aria-label={t('Search rooms')} placeholder={t('Search rooms')} value={query.q || ''} onChange={event => setQuery({ q: event.target.value || null }, { replace: true })} /><span>{index?.rooms.length || 0}</span></label>
      {error && <div className="rooms-error" role="alert"><p>{error}</p><button className="rooms-button rooms-quiet" disabled={refreshing} onClick={() => void refresh()}>{t('Retry')}</button></div>}
      <div className="rooms-list-scroll">{loading ? <p className="rooms-loading"><LoaderCircle size={18} className="rooms-spin" />{t('Loading rooms…')}</p> : rooms.map(room => <button key={room.id} className={`rooms-list-row ${selected === room.id ? 'is-selected' : ''}`} aria-current={selected === room.id ? 'page' : undefined} onClick={() => navigate(`rooms/${room.id}`)}><span className="rooms-list-row-glyph"><MessageSquare size={20} /></span><span className="rooms-list-row-main"><strong dir="auto">{room.name}</strong><span dir="auto">{room.members.map(member => member.name).join(', ') || t('No members')}</span><small>{t('{p0} members', [room.members.length])}</small></span></button>)}{!loading && enabled && !rooms.length && <div className="rooms-list-empty"><Users size={26} /><p>{query.q ? t('No rooms match your search.') : t('Your next great conversation starts here.')}</p>{query.q && <button className="rooms-button rooms-quiet" onClick={() => setQuery({ q: null }, { replace: true })}>{t('Clear search')}</button>}</div>}</div>
      <footer className="rooms-list-footer"><ShieldCheck size={15} /><span>{t('Your agents. One conversation.')}</span></footer>
    </aside>
    {loading ? <main className="rooms-center rooms-landing" aria-busy="true"><LoaderCircle size={25} className="rooms-spin" /></main> : !enabled ? <main className="rooms-center rooms-landing"><div className="rooms-hero-icon"><Users size={38} /></div><span className="rooms-eyebrow">{t('A PLACE TO COLLABORATE')}</span><h2>{t('Better perspectives, together.')}</h2><p>{t('Bring your agents into one shared conversation. Give each a role, choose who responds, and stay in control of their actions.')}</p>{error && <div className="rooms-error" role="alert">{error}<button className="rooms-button rooms-quiet" disabled={refreshing} onClick={() => void refresh()}>{t('Retry')}</button></div>}{index && <button className="rooms-button rooms-primary" disabled={enabling} onClick={() => { setEnabling(true); void roomsApi.enable().then(refresh).catch(cause => setError(errorMessage(cause))).finally(() => setEnabling(false)) }}>{enabling ? t('Enabling…') : t('Enable rooms')}<ArrowUp size={16} /></button>}</main> : creating ? <main className="rooms-create-page">{agentError && <div className="rooms-error" role="alert">{agentError}<button className="rooms-button rooms-quiet" onClick={() => void refresh()}>{t('Retry')}</button></div>}<RoomEditor agents={agents} maxMembers={index?.max_members ?? 8} onClose={goBack} onSave={async (name, members: RoomMember[]) => { const result = await roomsApi.create(name, members); changed(result.room); navigate(`rooms/${result.room.id}`) }} /></main> : selected ? <RoomConversation key={`${roomsAccountScope()}:${selected}`} id={selected} agents={agents} maxMembers={index?.max_members ?? 8} roundBudget={index?.round_budget ?? 8} onBack={goBack} onChanged={changed} onDeleted={() => { setIndex(current => current ? { ...current, rooms: current.rooms.filter(room => room.id !== selected) } : current); goBack() }} /> : <main className="rooms-center rooms-landing"><div className="rooms-hero-icon"><Users size={38} /></div><span className="rooms-eyebrow">{t('MORE MINDS, SHARED CONTEXT')}</span><h2>{t('Make room for your best ideas.')}</h2><p>{t('A researcher, a strategist, a second pair of eyes. Bring the right agents together and let the conversation build.')}</p><button className="rooms-button rooms-primary" onClick={() => navigate('rooms/new')}><Plus size={17} />{t('Create a room')}</button><div className="rooms-landing-benefits"><span><MessageSquare size={16} />{t('Shared history')}</span><span><AtSign size={16} />{t('Directed mentions')}</span><span><ShieldCheck size={16} />{t('Human control')}</span></div></main>}
  </div>
}
