import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type InboxItem, type InboxStatus } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { useChatSocket } from '../../shared/data/useChatSocket'
import { confirm } from '../../shared/ui/dialog'
import { reportActionFailure, reportingWrite } from '../../app/shell/reportingWrite'
import { isOpen, kindMeta, ITEM_KINDS } from './inboxMeta'

export function useInboxOperation(identity: string) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const active = useRef<object | null>(null)
  useEffect(() => { active.current = null; setBusy(null); setErr(''); return () => { active.current = null } }, [identity])
  const run = async <Result,>(tag: string, request: () => Promise<Result>, accept: (result: Result) => void, fallback: string) => {
    if (active.current) return
    const ticket = {}; active.current = ticket; setBusy(tag); setErr('')
    try { const result = await request(); if (active.current === ticket) accept(result) }
    catch (failure) { if (active.current === ticket) setErr(failure instanceof Error ? failure.message : fallback) }
    finally { if (active.current === ticket) { active.current = null; setBusy(null) } }
  }
  return { busy, err, setErr, run }
}
export function acceptsInboxFilter(item: InboxItem, filter: string): boolean {
  switch (filter) {
    case 'all': return true
    case 'favorites': return Boolean(item.favorited)
    case 'open': return isOpen(item.status)
    case 'handled': return ['handled', 'sent', 'dismissed'].includes(item.status)
    case 'filtered': return item.status === 'filtered'
    default: return item.classification === filter && isOpen(item.status)
  }
}
export function projectInbox(items: InboxItem[] | undefined, filter: string, kind: string, query: string) {
  const inKind = (item: InboxItem) => !kind || (item.item_kind || 'message') === kind
  const needle = query.trim().toLowerCase()
  const scoped = items?.filter(inKind)
  const filtered = scoped?.filter(item => acceptsInboxFilter(item, filter) && (!needle || `${item.sender_name} ${item.channel_name} ${item.message} ${kindMeta(item.item_kind).label}`.toLowerCase().includes(needle))) ?? null
  const counts = new Map<string, number>()
  for (const item of items ?? []) {
    const key = item.item_kind || 'message'
    counts.set(key, (counts.get(key) ?? 0) + Number(isOpen(item.status)))
  }
  return { filtered, filterCount: (key: string) => scoped?.filter(item => acceptsInboxFilter(item, key)).length, kindChips: ITEM_KINDS.flatMap(kind => counts.has(kind.key) ? [{ ...kind, open: counts.get(kind.key)! }] : []) }
}
export function useInboxQueue(openId: string | null, setOpenId: (id: string) => void) {
  const { data: items, error: itemsErr, stale: itemsStale, refresh: refreshItems } = useQuery<InboxItem[]>('inbox:items', () => api.inbox(), { persist: false })
  const { data: status, refresh: refreshStatus } = useQuery<InboxStatus | null>('inbox:status', () => api.inboxStatus().catch(() => null), { persist: false })
  const operation = useInboxOperation('inbox-queue')
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const load = () => { refreshItems(); refreshStatus() }
  const reload = () => { for (const key of ['inbox:items', 'inbox:status']) invalidateKeys(key); load() }
  useChatSocket(message => { if (['inbox_item_updated', 'inbox_new_item'].includes(message.type)) load() })
  const open = items?.find(item => item.id === openId) ?? null
  const observed = useRef<string | null>(null)
  useEffect(() => {
    if (!openId) { observed.current = null; return }
    if (!open || observed.current === openId) return
    observed.current = openId
    void api.openInboxItem(openId).catch(() => {})
    if (open.status === 'pending') void api.markInboxSeen({ ids: [openId] }).then(refreshItems).catch(() => {})
  }, [openId, open])
  useEffect(() => () => { if (reloadTimer.current) clearTimeout(reloadTimer.current) }, [])
  const watched = useMemo(() => {
    const channels = new Map((status?.watched_channels ?? []).map(channel => [channel.id, channel]))
    for (const item of items ?? []) if (item.channel && item.channel !== 'agent' && item.source !== 'digest' && !channels.has(item.channel)) channels.set(item.channel, { id: item.channel, name: item.channel_name || item.channel })
    return [...channels.values()]
  }, [status, items])
  const dismissAll = () => operation.run('dismiss', async () => {
    const count = status?.pending_count ?? 0
    if (!await confirm({ title: `Dismiss all ${count} pending item${count === 1 ? '' : 's'}?`, body: 'Every pending item of every kind is dismissed at once. There is no undo — but they stay readable under Handled.', danger: true, confirmLabel: 'Dismiss all' })) return false
    return reportingWrite(`dismiss ${count === 1 ? 'this item' : `all ${count} items`}`, () => api.dismissAllInbox())
  }, accepted => { if (accepted) reload() }, 'Dismiss failed')
  const restart = () => operation.run('restart', () => reportingWrite('restart the inbox sources', async () => {
    const response = await api.restartInbox()
    if (!response?.ok) throw new Error(response?.error || 'the gateway declined to restart')
  }), accepted => { if (accepted) { if (reloadTimer.current) clearTimeout(reloadTimer.current); reloadTimer.current = setTimeout(reload, 800) } }, 'Restart failed')
  const digest = (channel: string) => operation.run('digest', () => api.digestInboxChannel(channel).catch(failure => { reportActionFailure('generate the digest')(failure); return null }), result => { if (result) { reload(); if (result.id) setOpenId(result.id) } }, 'Digest failed')
  return { items, itemsErr, itemsStale, status, open, load, reload, watched, busy: Boolean(operation.busy), dismissAll, restart, digest }
}
export function useInboxDetailActions(item: InboxItem, onChanged: () => void) {
  const operation = useInboxOperation(item.id)
  const [draft, setDraft] = useState(item.draft ?? '')
  useEffect(() => { setDraft(item.draft ?? '') }, [item.id])
  const patch = (body: Record<string, unknown>, tag: string) => operation.run(tag, () => api.updateInboxItem(item.id, body), onChanged, 'Update failed')
  const generate = () => operation.run('draft', () => api.draftInboxReply(item.id), result => { setDraft(result.draft ?? ''); onChanged() }, 'Draft failed')
  const send = () => {
    const content = draft.trim()
    if (!content) { operation.setErr('Write a reply first'); return }
    void operation.run('send', () => api.sendInboxReply(item.id, content), onChanged, 'Send failed')
  }
  return { ...operation, draft, setDraft, patch, generate, send, fav: () => operation.run('fav', () => api.favoriteInboxItem(item.id, !item.favorited), onChanged, 'Favorite failed'), restore: () => operation.run('restore', () => api.restoreInboxItem(item.id), onChanged, 'Restore failed') }
}
