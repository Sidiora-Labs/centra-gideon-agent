import { gatewayRequest, requestDelete, requestJson, responseError } from '../../shared/data/gatewayRequest'

export interface RoomMember {
  id: string
  agent: string
  name: string
  role: string
  listen_policy: 'all' | 'mentions' | 'none'
  profile_narrowing?: Record<string, unknown>
}
export interface Room {
  id: string
  name: string
  members: RoomMember[]
  created_at: string
  updated_at: string
}
export interface RoomMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  speaker?: string
  speaker_name?: string
  created_at?: string
  ts?: string | number
}
export interface RoomTurn {
  id: string
  room_id: string
  status: 'queued' | 'running' | 'completed' | 'cancelled' | 'failed'
  member_id: string | null
  text: string
  error: string | null
  created_at: string
  updated_at: string
}
export interface RoomsIndex { rooms: Room[]; enabled: boolean; max_members: number; round_budget: number }
export interface RoomTranscript { messages: RoomMessage[]; has_more: boolean; before: string | null }
const path = (id: string) => `/api/rooms/${encodeURIComponent(id)}`
export const roomsApi = {
  list: () => requestJson<RoomsIndex>('/api/rooms'),
  enable: () => requestJson('/api/rooms/enable', 'POST', {}),
  create: (name: string, members: RoomMember[]) => requestJson<{ room: Room }>('/api/rooms', 'POST', { name, members }),
  detail: (id: string) => requestJson<{ room: Room; member_postures: unknown }>(path(id)),
  update: (id: string, name: string, members: RoomMember[]) => requestJson<{ room: Room }>(path(id), 'PATCH', { name, members }),
  remove: (id: string) => requestDelete(path(id)),
  transcript: (id: string, before?: string) => requestJson<RoomTranscript>(`${path(id)}/transcript${before ? `?before=${encodeURIComponent(before)}` : ''}`),
  turn: (id: string) => requestJson<{ turn: RoomTurn | null }>(`${path(id)}/turn`),
  send: (id: string, text: string, requestId: string) => requestJson<{ turn: RoomTurn }>(`${path(id)}/turns`, 'POST', { text, request_id: requestId }),
  cancel: (id: string) => requestJson<{ turn: RoomTurn }>(`${path(id)}/cancel`, 'POST', {}),
  async export(id: string, format: 'json' | 'md'): Promise<void> {
    const response = await gatewayRequest(`${path(id)}/export?format=${format}`)
    if (!response.ok) throw await responseError(response)
    const href = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = `room-${id}.${format}`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(href), 1000)
  },
}
