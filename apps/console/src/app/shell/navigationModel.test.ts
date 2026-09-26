import { describe, expect, it } from 'vitest'
import { parseRouteHash } from './useHashRoute'
import { activeNavigationId, FAMILIAR_IDS, managesApps, navigationItems, recentSessionItems, ROUTABLE_ROOTS } from './navigationModel'
import { capabilityAreas } from '../../features/capabilities/navigation'
import type { ChatSessionSummary } from '../../shared/data/api'

const route = (id: string) => parseRouteHash(`#/${id}`, 'dashboard')

function selected(id: string) {
  const snapshot = route(id)
  return activeNavigationId(snapshot.route, snapshot.sub, snapshot.query)
}

describe('the shared navigation route model', () => {
  it('takes every familiar destination to a real hash route with a distinct label', () => {
    const items = navigationItems()
    expect(new Set(items.map(item => item.id)).size).toBe(items.length)
    for (const id of FAMILIAR_IDS) {
      const item = items.find(item => item.id === id)
      expect(item?.label).toBeTruthy()
      expect(ROUTABLE_ROOTS.has(route(id).route)).toBe(true)
    }
    expect(items.find(item => item.id === 'chat/new')?.label).toBe('New conversation')
    expect(items.find(item => item.id === 'chat/history')?.label).toBe('Conversations')
    expect(items.find(item => item.id === 'capabilities/communications?view=calendar')?.label).toBe('Calendar')
    expect(route('capabilities/communications?view=calendar')).toMatchObject({ route: 'capabilities', sub: 'communications', query: { view: 'calendar' } })
  })

  it('keeps all prior root destinations and capability areas routable', () => {
    for (const id of ['dashboard', 'chat', 'rooms', 'projects', 'knowledge', 'tasks', 'inbox', 'triggers', 'files', 'artifacts', 'terminal',
      'agents', 'tools', 'skills', 'learning', 'prompts', 'workflows', 'experiments', 'apps', 'settings', 'notifications', 'discover',
      'loop', 'loops', 'code', 'app', 'mission-control']) expect(ROUTABLE_ROOTS.has(id)).toBe(true)
    for (const area of capabilityAreas) {
      const id = `capabilities/${area.id}`
      expect(navigationItems().some(item => item.id === id)).toBe(true)
      expect(selected(`${id}/detail`)).toBe(id)
    }
  })

  it('maps existing conversation, project, installed app and calendar deep links to their visible destination', () => {
    expect(selected('chat')).toBe('chat/new')
    expect(selected('chat/new')).toBe('chat/new')
    expect(selected('chat/history')).toBe('chat/history')
    expect(selected('chat/session%20key')).toBe('chat/session%20key')
    expect(selected('loop/run-1')).toBe('projects')
    expect(selected('loops')).toBe('projects')
    expect(selected('code/task-1')).toBe('projects')
    expect(selected('app/weather/page')).toBe('app/weather')
    expect(selected('capabilities')).toBe('capabilities')
    expect(selected('capabilities/communications?view=calendar')).toBe('capabilities/communications?view=calendar')
    expect(selected('capabilities/communications?view=people')).toBe('capabilities/communications')
    expect(selected('projects/detail')).toBe('projects')
  })

  it('keeps root apps as the collection and all management links on their existing screen', () => {
    expect(selected('apps')).toBe('apps')
    expect(selected('apps/manage')).toBe('apps/manage')
    expect(selected('apps?view=store')).toBe('apps/manage')
    expect(selected('apps?view=connected')).toBe('apps/manage')
    expect(selected('apps/legacy')).toBe('apps/legacy')
    expect(managesApps('', {})).toBe(false)
    expect(managesApps('manage', {})).toBe(true)
    expect(managesApps('legacy', {})).toBe(true)
    expect(managesApps('', { view: 'store' })).toBe(true)
  })

  it('adds hosted Connections only in hosted navigation and translates visible labels', () => {
    const local = navigationItems()
    const hosted = navigationItems(true, value => `translated:${value}`)
    expect(local.some(item => item.id === 'connections')).toBe(false)
    expect(hosted.find(item => item.id === 'connections')?.label).toBe('translated:Connections')
    expect(hosted.find(item => item.id === 'triggers')?.label).toBe('translated:Automations')
    expect(local.find(item => item.id === 'triggers')?.label).toBe('Triggers')
    expect(hosted.find(item => item.id === 'apps')?.section).toBe('translated:Your apps')
    expect(hosted.find(item => item.id === 'settings')?.pinBottom).toBe(true)
  })

  it('shows only three actual manual recent sessions in activity order with safe routes', () => {
    const sessions: ChatSessionSummary[] = [
      { key: 'old', title: 'Old', messages: 1, last_ts: '2026-09-01T00:00:00Z' },
      { key: 'active with space', title: 'Current', messages: 2, last_ts: '2026-09-25T00:00:00Z' },
      { key: 'middle', title: 'Middle', messages: 3, last_ts: '2026-09-20T00:00:00Z' },
      { key: 'newest', title: 'Newest', messages: 4, last_ts: '2026-09-26T00:00:00Z' },
      { key: 'loop', title: 'Loop', messages: 2, origin: 'loop', last_ts: '2026-09-27T00:00:00Z' },
      { key: 'archived', title: 'Archived', messages: 2, lifecycle: 'archived', last_ts: '2026-09-28T00:00:00Z' },
    ]
    expect(recentSessionItems(undefined)).toEqual([])
    const recent = recentSessionItems(sessions, value => `translated:${value}`)
    expect(recent.map(item => item.id)).toEqual(['chat/newest', 'chat/active%20with%20space', 'chat/middle'])
    expect(recent.map(item => item.label)).toEqual(['Newest', 'Current', 'Middle'])
    expect(recent.every(item => item.section === 'translated:Recent')).toBe(true)
    expect(sessions[0].key).toBe('old')
  })
})
