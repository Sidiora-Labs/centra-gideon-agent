import { describe, expect, it } from 'vitest'
import { owedMembers, roundRunning, type Room, type RoomTurn } from './roomsApi'

const room: Room = { id: 'review', name: 'Review', created_at: '', updated_at: '', members: [
  { id: 'analyst', agent: 'default', name: 'Analyst', role: '', listen_policy: 'all' },
  { id: 'skeptic', agent: 'default', name: 'Skeptic', role: '', listen_policy: 'all' },
] }
const turn: RoomTurn = { id: 'round', room_id: 'review', status: 'running', member_id: 'analyst',
  text: '', error: null, created_at: '', updated_at: '' }

describe('room round recovery', () => {
  it('keeps the open member first once, then the remaining FIFO roster', () => {
    expect(owedMembers({ ...room, speaking: 'analyst', pending_queue: ['skeptic', 'analyst', 'removed'] })).toEqual(['analyst', 'skeptic'])
  })
  it('polls a live round rather than a persisted running status', () => {
    expect(roundRunning(turn)).toBe(false)
    expect(roundRunning({ ...turn, round_running: true })).toBe(true)
    expect(roundRunning({ ...turn, status: 'paused', round_running: false })).toBe(false)
  })
  it('does not invent owed members for an idle or legacy room', () => {
    expect(owedMembers(room)).toEqual([])
    expect(owedMembers(null)).toEqual([])
    expect(roundRunning(null)).toBe(false)
  })
})
