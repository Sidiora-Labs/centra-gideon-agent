import { useEffect, useRef, useState } from 'react'
import { Check, ChevronDown, Plus, ShieldCheck, Trash2, Users, X } from 'lucide-react'
import type { SavedAgent } from '../../shared/data/api'
import type { Room, RoomMember } from './roomsApi'
import { newMemberId, validRoom } from './roomsState'
import { t } from './roomsText'

export function RoomEditor({ room, agents, maxMembers, busy = false, onClose, onSave, onDelete }: {
  room?: Room
  agents: SavedAgent[]
  maxMembers: number
  busy?: boolean
  onClose: () => void
  onSave: (name: string, members: RoomMember[]) => Promise<void>
  onDelete?: () => Promise<void>
}) {
  const [name, setName] = useState(room?.name ?? '')
  const [members, setMembers] = useState<RoomMember[]>(() => room?.members.map(member => ({ ...member })) ?? [])
  const [query, setQuery] = useState('')
  const [picker, setPicker] = useState(!room)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [confirm, setConfirm] = useState(false)
  const nameRef = useRef<HTMLInputElement>(null)
  useEffect(() => { nameRef.current?.focus() }, [])
  const selected = new Set(members.map(member => member.agent))
  const available = agents.filter(agent => !agent.reserved && `${agent.name} ${agent.description || ''}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()))
  const patch = (id: string, value: Partial<RoomMember>) => setMembers(current => current.map(member => member.id === id ? { ...member, ...value } : member))
  const toggle = (agent: SavedAgent) => {
    if (selected.has(agent.name)) setMembers(current => current.filter(member => member.agent !== agent.name))
    else if (members.length < maxMembers) setMembers(current => [...current, { id: newMemberId(agent.name), agent: agent.name, name: agent.name, role: '', listen_policy: 'all', profile_narrowing: { approval: 'ask' } }])
  }
  const perform = async (operation: () => Promise<void>) => {
    if (saving || busy) return
    setSaving(true)
    setError('')
    try { await operation() } catch (cause) { setError(cause instanceof Error ? cause.message : t('Could not save the room. Please try again.')) } finally { setSaving(false) }
  }
  return <section className="rooms-editor" aria-label={room ? t('Room settings') : t('New room')} onKeyDown={event => { if (event.key === 'Escape' && !saving) onClose() }}>
    <header className="rooms-pane-heading"><div><span className="rooms-eyebrow">{t('SHARED SPACE')}</span><h2>{room ? t('Room settings') : t('Create a room')}</h2></div><button type="button" className="rooms-icon" aria-label={t('Close room settings')} onClick={onClose} disabled={saving}><X size={18} /></button></header>
    <form onSubmit={event => { event.preventDefault(); if (validRoom(name, members, maxMembers)) void perform(() => onSave(name.trim(), members)) }}>
      <div className="rooms-editor-body">
        {error && <p className="rooms-error" role="alert">{error}</p>}
        {busy && <p className="rooms-notice">{t('Stop the current turn before changing this room.')}</p>}
        <label className="rooms-field">{t('Room name')}<input ref={nameRef} value={name} maxLength={500} required onChange={event => setName(event.target.value)} placeholder={t('For example, Product studio')} disabled={busy || saving} /></label>
        <div className="rooms-field-heading"><h3>{t('Members')} <span>{members.length}/{maxMembers}</span></h3><button type="button" className="rooms-button rooms-quiet" onClick={() => setPicker(value => !value)} disabled={busy || saving}><Plus size={15} />{t('Add members')}</button></div>
        <p className="rooms-hint">{t('Each agent brings its own expertise. Members respond in the order shown.')}</p>
        {picker && <div className="rooms-picker">
          <input aria-label={t('Find an agent')} placeholder={t('Find an agent')} value={query} onChange={event => setQuery(event.target.value)} />
          <div className="rooms-picker-options">
            {available.map(agent => <button type="button" key={agent.name} className="rooms-picker-option" aria-pressed={selected.has(agent.name)} onClick={() => toggle(agent)} disabled={busy || saving || (!selected.has(agent.name) && members.length >= maxMembers)}>
              <span className="rooms-avatar">{agent.name.slice(0, 2).toLocaleUpperCase()}</span><span><strong dir="auto">{agent.name}</strong>{agent.description && <small dir="auto">{agent.description}</small>}</span><span className={`rooms-checkbox ${selected.has(agent.name) ? 'selected' : ''}`}>{selected.has(agent.name) && <Check size={13} />}</span>
            </button>)}
            {!available.length && <p className="rooms-hint">{agents.length ? t('No agents match your search.') : t('Create an agent in Agents to add it to a room.')}</p>}
          </div>
        </div>}
        <div className="rooms-member-settings">
          {members.map((member, index) => <div className="rooms-member-setting" key={member.id}>
            <button type="button" className="rooms-member-summary" aria-expanded={expanded === member.id} onClick={() => setExpanded(expanded === member.id ? null : member.id)}><span className="rooms-member-order">{index + 1}</span><span><strong dir="auto">{member.name || member.agent}</strong><small>{member.listen_policy === 'all' ? t('Responds to every message') : member.listen_policy === 'mentions' ? t('Responds when mentioned') : t('Paused')}</small></span><ChevronDown size={16} /></button>
            {expanded === member.id && <div className="rooms-member-fields">
              <label className="rooms-field">{t('Display name')}<input value={member.name} required maxLength={500} onChange={event => patch(member.id, { name: event.target.value })} disabled={busy || saving} /></label>
              <label className="rooms-field">{t('Role in this room')}<textarea value={member.role} maxLength={4000} rows={3} placeholder={t('For example, review proposals and challenge assumptions')} onChange={event => patch(member.id, { role: event.target.value })} disabled={busy || saving} /></label>
              <label className="rooms-field">{t('When to respond')}<select aria-label={t('When to respond')} value={member.listen_policy} onChange={event => patch(member.id, { listen_policy: event.target.value as RoomMember['listen_policy'] })} disabled={busy || saving}><option value="all">{t('Every message')}</option><option value="mentions">{t('Only when mentioned')}</option><option value="none">{t('Paused')}</option></select></label>
              <p className="rooms-hint">{t('Agent')}: <bdi>{member.agent}</bdi></p>
              <button type="button" className="rooms-button rooms-danger rooms-quiet" disabled={busy || saving} onClick={() => setMembers(current => current.filter(item => item.id !== member.id))}><X size={15} />{t('Remove member')}</button>
            </div>}
          </div>)}
          {!members.length && <div className="rooms-empty-members"><Users size={23} /><p>{t('Choose the agents you want in the conversation.')}</p></div>}
        </div>
        <div className="rooms-safety"><ShieldCheck size={18} /><p>{t('New members start with read-only tools and human approval. Room roles never grant extra permissions.')}</p></div>
        {onDelete && <div className="rooms-delete">
          {confirm ? <><p>{t('Delete this room and its shared conversation? This cannot be undone.')}</p><div className="rooms-actions"><button type="button" className="rooms-button rooms-danger" disabled={busy || saving} onClick={() => void perform(onDelete)}>{t('Delete permanently')}</button><button type="button" className="rooms-button rooms-quiet" onClick={() => setConfirm(false)} disabled={saving}>{t('Keep room')}</button></div></> : <button type="button" className="rooms-button rooms-quiet rooms-danger" onClick={() => setConfirm(true)} disabled={busy || saving}><Trash2 size={15} />{t('Delete room')}</button>}
        </div>}
      </div>
      <footer className="rooms-editor-footer"><button type="button" className="rooms-button rooms-quiet" disabled={saving} onClick={onClose}>{t('Cancel')}</button><button type="submit" className="rooms-button rooms-primary" disabled={busy || saving || !validRoom(name, members, maxMembers)}>{saving ? t('Saving…') : room ? t('Save changes') : t('Create room')}</button></footer>
    </form>
  </section>
}
