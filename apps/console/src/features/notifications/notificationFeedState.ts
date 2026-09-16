import { useEffect, useMemo, useRef, useState } from 'react'
import { api, type NotificationItem } from '../../shared/data/api'
import { invalidateKeys, useQuery } from '../../shared/data/data'
import { useChatSocket } from '../../shared/data/useChatSocket'
import { useAutonomyLadder } from '../../shared/data/rungs'
import { confirm, confirmDelete } from '../../shared/ui/dialog'
import { notify } from '../../app/shell/appSdk'
import { bucketOf, kindsPresent } from './notificationMeta'

const describeFailure = (error: unknown) => error instanceof Error ? error.message : String(error)
export function projectNotifications(items: NotificationItem[] | undefined, filter: string, now: number) {
  const filtered = items ? [...items].reverse().filter(item => filter === 'all' || (filter === 'unread' ? !item.acked : (item.kind || 'info') === filter)) : null
  const groups: Record<string, NotificationItem[]> = {}
  const counts = new Map<string, number>()
  let unread = 0
  for (const item of items ?? []) { const kind = item.kind || 'info'; counts.set(kind, (counts.get(kind) || 0) + 1); unread += Number(!item.acked) }
  for (const item of filtered ?? []) { const bucket = bucketOf(item.ts, now); (groups[bucket] ??= []).push(item) }
  return { filtered, groups, unread, kinds: kindsPresent(items ?? []), counts }
}
export function useNotificationFeed(filter: string, openTs: string | null, setOpenTs: (value: string) => void) {
  const { data: items, error: loadErr, refresh } = useQuery('notifications', () => api.notifications().then(result => result.notifications), { persist: false })
  const [now, setNow] = useState(Date.now)
  const operations = useRef(new Set<string>())
  const { ladder, refresh: refreshLadder } = useAutonomyLadder()
  const load = () => { invalidateKeys('notifications'); refresh() }
  useEffect(() => {
    const timer = window.setInterval(() => { setNow(Date.now()); refresh() }, 10000)
    return () => window.clearInterval(timer)
  }, [refresh])
  useChatSocket(event => { if (event.type.startsWith('notification')) refresh() })
  const projection = useMemo(() => projectNotifications(items, filter, now), [items, filter, now])
  const undoable = useMemo(() => new Set((ladder?.reversals ?? []).flatMap(record => record.reversed_at ? [] : [record.id])), [ladder])
  const perform = async (key: string, work: () => Promise<void>) => {
    if (operations.current.has(key)) return
    operations.current.add(key)
    try { await work() } finally { operations.current.delete(key) }
  }
  const ack = (item: NotificationItem) => perform(`read:${item.ts}`, async () => {
    await api.ackNotification(item.ts).catch((error) => notify(`Couldn't mark this notification read: ${describeFailure(error)}`, 'error'))
    load()
  })
  const unack = (item: NotificationItem) => perform(`read:${item.ts}`, async () => {
    await api.unackNotification(item.ts).catch((error) => notify(`Couldn't mark this notification unread: ${describeFailure(error)}`, 'error'))
    load()
  })
  const ackAll = () => perform('read:all', async () => {
    await api.ackAllNotifications().catch((error) => notify(`Couldn't mark all notifications read: ${describeFailure(error)}`, 'error'))
    load()
  })
  const remove = (item: NotificationItem) => perform(`remove:${item.ts}`, async () => {
    if (!await confirmDelete('notification', item.title || undefined)) return
    const deleted = await api.deleteNotification(item.ts).then(() => true).catch((error) => { notify(`Couldn't delete this notification: ${describeFailure(error)}`, 'error'); return false })
    if (!deleted) return
    if (openTs === item.ts) setOpenTs('')
    load()
  })
  const clearAll = () => perform('remove:all', async () => {
    const total = items?.length ?? 0
    if (!await confirm({ title: `Clear all ${total} notification${total === 1 ? '' : 's'}?`, body: 'The whole history is removed from disk, including any hidden by the current filter. This cannot be undone.', danger: true, confirmLabel: 'Clear all' })) return
    const cleared = await api.clearNotifications().then(() => true).catch((error) => { notify(`Couldn't clear notifications: ${describeFailure(error)}`, 'error'); return false })
    if (!cleared) return
    setOpenTs(''); load()
  })
  const undoAction = (item: NotificationItem) => perform(`undo:${item.reversal_id}`, async () => {
    if (!item.reversal_id) return
    try {
      await api.autonomyUndo(item.reversal_id)
      notify(`Undone. ${item.action_type || 'That action'} will not do this on its own any more.`, 'success')
    } catch (error) { notify(`Couldn't undo: ${describeFailure(error)}`, 'error') }
    invalidateKeys('autonomy:ladder'); refreshLadder()
  })
  return { items, loadErr, now, ...projection, open: items?.find(item => item.ts === openTs) ?? null, undoable, load, ack, unack, ackAll, remove, clearAll, undoAction }
}
