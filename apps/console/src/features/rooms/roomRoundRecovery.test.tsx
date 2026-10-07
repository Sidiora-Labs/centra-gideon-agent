import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api } from '../../shared/data/api'
import { RoomsSection } from './RoomsSection'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { roomsApi, owedMembers, roundRunning, type Room, type RoomTurn } from './roomsApi'

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


afterEach(() => vi.restoreAllMocks())

describe('native room settings controls', () => {
  it('preserves the actual settings button ref and focus restoration after native adoption', async () => {
    const match = window.matchMedia.bind(window)
    vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
      ...match(query), matches: query === '(max-width: 1200px)',
    }))
    vi.spyOn(roomsApi, 'list').mockResolvedValue({ rooms: [room], enabled: true, max_members: 8, round_budget: 8 })
    vi.spyOn(api, 'agents').mockResolvedValue({ agents: [], default_agent: 'default' })
    vi.spyOn(api, 'approvals').mockResolvedValue([])
    vi.spyOn(roomsApi, 'detail').mockResolvedValue({ room, member_postures: [] })
    vi.spyOn(roomsApi, 'transcript').mockResolvedValue({ messages: [], has_more: false, before: null })
    vi.spyOn(roomsApi, 'turn').mockResolvedValue({ turn: null })
    render(<RoomsSection navEpoch={0} sub="review" navigate={() => {}} query={{}} setQuery={() => {}} />)
    const opener = await screen.findByRole('button', { name: 'Room settings' })
    expect(opener.tagName).toBe('BUTTON')
    opener.focus()
    expect(document.activeElement).toBe(opener)
    fireEvent.click(opener)
    expect(await screen.findByRole('dialog', { name: 'Room settings' })).toBeInTheDocument()
    expect(opener).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Close room settings' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Room settings' })).toBeNull())
    await waitFor(() => expect(document.activeElement).toBe(opener))
    expect(opener).toHaveAttribute('aria-expanded', 'false')
    expect(opener.classList.contains('!h-11')).toBe(true)
    expect(opener.classList.contains('!w-11')).toBe(true)
  })
})
