import { afterEach, describe, expect, it } from 'vitest'
import { changedDraft, clearRoomDrafts, draftKey, filterRooms, insertMention, isActiveTurn, mentionAt, mentionLabel, newMemberId, readDraft, roomApprovals, validRoom, writeDraft } from './roomsState'
import type { Room, RoomMember, RoomTurn } from './roomsApi'
import { t } from './roomsText'

const researcher: RoomMember = { id: 'researcher-1', name: 'Research lead', agent: 'researcher', role: 'Find evidence', listen_policy: 'mentions', profile_narrowing: { approval: 'ask' } }
const reviewer: RoomMember = { id: 'reviewer-1', name: 'Reviewer', agent: 'reviewer', role: 'Challenge assumptions', listen_policy: 'all' }
const room: Room = { id: 'product', name: 'Product studio', members: [researcher, reviewer], created_at: '2026-09-24T10:00:00Z', updated_at: '2026-09-24T12:00:00Z' }

afterEach(() => { localStorage.clear(); clearRoomDrafts() })

describe('room composer continuity', () => {
  it('keeps a retry identity across reading and resaving a failed-send draft', () => {
    const key = draftKey('account-a', 'product')
    const draft = changedDraft(readDraft(key), 'Please compare the evidence')
    writeDraft(key, draft)
    expect(readDraft(key)).toEqual(draft)
    expect(changedDraft(draft, draft.text)).toBe(draft)
    const edited = changedDraft(draft, 'Please compare the newer evidence')
    expect(edited.requestId).not.toBe(draft.requestId)
    writeDraft(key, edited)
    expect(readDraft(key)).toEqual(edited)
  })

  it('isolates drafts by account and room and does not resurrect drafts cleared at logout', () => {
    const first = draftKey('account:a', 'room:b')
    const otherAccount = draftKey('account', 'a:room:b')
    expect(first).not.toBe(otherAccount)
    writeDraft(first, { text: 'Private to this account', requestId: 'one' })
    expect(readDraft(otherAccount).text).toBe('')
    expect(readDraft(draftKey('account:a', 'other-room')).text).toBe('')
    localStorage.removeItem(first)
    expect(readDraft(first).text).toBe('')
  })

  it('ignores malformed persistent drafts', () => {
    const key = draftKey('local', 'a')
    for (const corrupt of ['{', 'null', '{"text":9,"requestId":"a"}', '{"text":"hello"}']) {
      localStorage.setItem(key, corrupt)
      expect(readDraft(key).text).toBe('')
    }
  })
})

describe('room mentions and selection', () => {
  it('offers Unicode mentions at the caret without interpreting email addresses', () => {
    expect(mentionAt('Ask @研究', 7)).toEqual({ start: 4, query: '研究' })
    expect(mentionAt('hello @rev please', 10)).toEqual({ start: 6, query: 'rev' })
    expect(mentionAt('name@example.com', 12)).toBeNull()
    expect(mentionAt('@@rev', 5)).toBeNull()
  })

  it('inserts a readable multi-word mention while preserving text after the caret', () => {
    expect(insertMention('Ask @res to check', 8, 'Research lead')).toEqual({ text: 'Ask @Research lead  to check', cursor: 19 })
    expect(insertMention('@eve', 4, 'everyone')).toEqual({ text: '@everyone ', cursor: 10 })
    expect(mentionLabel(researcher, [researcher, reviewer])).toBe('Research lead')
    expect(mentionLabel(researcher, [researcher, { ...reviewer, name: 'research LEAD' }])).toBe(researcher.id)
  })

  it('creates backend-valid readable IDs for ASCII and non-Latin agents', () => {
    expect(newMemberId('Research lead')).toMatch(/^Research-lead-[a-f0-9]{8}$/)
    expect(newMemberId('研究员')).toMatch(/^member-[a-f0-9]{8}$/)
    expect(newMemberId('x'.repeat(100))).toHaveLength(64)
  })

  it('validates room limits, names and independently scoped member identities', () => {
    expect(validRoom('Product', [researcher, reviewer], 8)).toBe(true)
    expect(validRoom(' ', [researcher], 8)).toBe(false)
    expect(validRoom('Product', [], 8)).toBe(false)
    expect(validRoom('Product', [researcher, reviewer], 1)).toBe(false)
    expect(validRoom('Product', [researcher, researcher], 8)).toBe(false)
    expect(validRoom('Product', [{ ...researcher, role: 'x'.repeat(4001) }], 8)).toBe(false)
  })

  it('searches member roles and keeps recent rooms first without mutating server state', () => {
    const earlier = { ...room, id: 'earlier', updated_at: '2026-09-23T00:00:00Z' }
    const rooms = [earlier, room]
    expect(filterRooms(rooms, 'EVIDENCE').map(item => item.id)).toEqual(['product', 'earlier'])
    expect(filterRooms(rooms, 'no-match')).toEqual([])
    expect(rooms[0].id).toBe('earlier')
  })

  it('uses complete count phrases for translation', () => {
    expect(t('{p0} members', [2])).toBe('2 members')
    expect(t('Up to {p0} replies per message', [8])).toBe('Up to 8 replies per message')
  })
})

describe('room turn controls', () => {
  it('only marks queued and running turns active', () => {
    const turn: RoomTurn = { id: 'turn-1', room_id: 'product', status: 'running', member_id: 'researcher-1', text: '', error: null, created_at: '', updated_at: '' }
    expect(isActiveTurn(null)).toBe(false)
    for (const status of ['queued', 'running', 'completed', 'cancelled', 'failed'] as const) expect(isActiveTurn({ ...turn, status })).toBe(status === 'queued' || status === 'running')
  })

  it('excludes other room and direct-chat approvals including prefix collisions', () => {
    const approvals = ['room:product:researcher-1', 'room:product-two:reviewer', 'dashboard:ui', 'room:product:reviewer-1'].map((session, index) => ({ id: String(index), session, source: 'room', tool: 'write_file', ts: 0 }))
    expect(roomApprovals(approvals, 'product').map(item => item.id)).toEqual(['0', '3'])
  })
})
