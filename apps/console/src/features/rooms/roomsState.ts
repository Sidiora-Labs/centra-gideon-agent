import type { PendingApproval } from '../../shared/data/api'
import type { Room, RoomMember, RoomTurn } from './roomsApi'

export function isActiveTurn(turn: RoomTurn | null): boolean {
  return turn?.status === 'queued' || turn?.status === 'running'
}

export function roomApprovals(approvals: PendingApproval[], roomId: string): PendingApproval[] {
  return approvals.filter(approval => approval.session.startsWith(`room:${roomId}:`))
}

export function filterRooms(rooms: Room[], query: string): Room[] {
  const search = query.trim().toLocaleLowerCase()
  return [...rooms].filter(room => !search || [room.name, ...room.members.map(member => `${member.name} ${member.agent} ${member.role}`)].some(text => text.toLocaleLowerCase().includes(search)))
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at))
}

export function validRoom(name: string, members: RoomMember[], maxMembers: number): boolean {
  return Boolean(name.trim()) && name.trim().length <= 500 && members.length > 0 && members.length <= maxMembers &&
    new Set(members.map(member => member.id)).size === members.length && members.every(member => Boolean(member.name.trim()) && member.name.length <= 500 && Boolean(member.agent.trim()) && member.role.length <= 4000)
}

export function mentionAt(text: string, cursor: number): { start: number; query: string } | null {
  const match = /(?:^|\s)@([\p{L}\p{N}_-]*)$/u.exec(text.slice(0, cursor))
  return match ? { start: cursor - match[1].length - 1, query: match[1].toLocaleLowerCase() } : null
}

export function insertMention(text: string, cursor: number, value: string): { text: string; cursor: number } {
  const start = mentionAt(text, cursor)?.start ?? cursor
  const before = text.slice(0, start)
  const mention = `@${value} `
  return { text: before + mention + text.slice(cursor), cursor: before.length + mention.length }
}

export function draftKey(scope: string, roomId: string): string {
  return `gideon.rooms.draft:${encodeURIComponent(scope)}:${encodeURIComponent(roomId)}`
}

export interface RoomDraft { text: string; requestId: string }
const volatileDrafts = new Map<string, RoomDraft>()
export function clearRoomDrafts(): void { volatileDrafts.clear() }

export function newMemberId(name: string): string {
  const stem = name.normalize('NFKD').replace(/[^A-Za-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 55) || 'member'
  return `${stem}-${crypto.randomUUID().slice(0, 8)}`
}

export function mentionLabel(member: RoomMember, members: RoomMember[]): string {
  const normalized = member.name.normalize('NFC').toLocaleLowerCase()
  return members.filter(item => item.name.normalize('NFC').toLocaleLowerCase() === normalized).length === 1 ? member.name : member.id
}

export function readDraft(key: string): RoomDraft {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(key) || 'null')
    if (saved && typeof saved === 'object' && 'text' in saved && typeof saved.text === 'string' && 'requestId' in saved && typeof saved.requestId === 'string') return saved as RoomDraft
    volatileDrafts.delete(key)
    return { text: '', requestId: crypto.randomUUID() }
  } catch { /* Storage can be unavailable in private browsing. */ }
  return volatileDrafts.get(key) ?? { text: '', requestId: crypto.randomUUID() }
}

export function writeDraft(key: string, draft: RoomDraft): void {
  volatileDrafts.set(key, draft)
  try { localStorage.setItem(key, JSON.stringify(draft)) } catch { /* Keep the current draft in memory. */ }
}

export function changedDraft(draft: RoomDraft, text: string): RoomDraft {
  return text === draft.text ? draft : { text, requestId: crypto.randomUUID() }
}
