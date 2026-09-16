import { describe, it, expect } from 'vitest'
import { createAppApi, type AppContext } from './appSdk'


const app: AppContext = {
  name: 'demo-dashboard',
  permissions: { api: ['/api/apps/demo-dashboard', '/api/projects', '/api/tasks', '/api/knowledge'] },
}

describe('createAppApi.can (client permission matcher)', () => {
  const api = createAppApi(app)

  it('matches a declared path with a query string (server parity)', () => {
    expect(api.can('/api/tasks?limit=100')).toBe(true)
    expect(api.can('/api/tasks?status=done&limit=30')).toBe(true)
    expect(api.can('/api/knowledge/items?limit=30')).toBe(true)
  })

  it('still matches bare and nested declared paths', () => {
    expect(api.can('/api/tasks')).toBe(true)
    expect(api.can('/api/projects')).toBe(true)
    expect(api.can('/api/apps/demo-dashboard')).toBe(true)
  })

  it('rejects undeclared paths, with or without a query string', () => {
    expect(api.can('/api/memory')).toBe(false)
    expect(api.can('/api/memory?q=x')).toBe(false)
    expect(api.can('/api/config')).toBe(false)
  })

  it('never lets a query string smuggle a prefix match', () => {
    expect(api.can('/api/memory?next=/api/tasks')).toBe(false)
  })

  it('always allows the app its own backend proxy', () => {
    expect(api.can('/apps/demo-dashboard/api/counters')).toBe(true)
    expect(api.can('/apps/demo-dashboard/api/counters?x=1')).toBe(true)
    expect(api.can('/apps/other-app/api/counters')).toBe(false)
  })
})
