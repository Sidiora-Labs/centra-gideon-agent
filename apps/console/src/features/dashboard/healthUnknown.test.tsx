import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const boom = () => Promise.reject(new Error('probe unreachable'))
const okReport = { ok: true, core_ok: true, worst: '', capabilities: {} }
const sickReport = { ok: false, core_ok: true, worst: 'memory', capabilities: { memory: { ok: false } } }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve({ update_available: false }),
      system: () => Promise.resolve({ platform: 'darwin' }),
      doctor: () => Promise.resolve(okReport),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ tips: [] }),
      approvals: () => Promise.resolve([]),
      inboxPending: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ entries: [] }),
      ...over,
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the dashboard health strip cannot mistake "could not probe" for "healthy"', () => {
  async function mountStrip() {
    const { DashboardLiveProvider } = await import('./DashboardLive')
    const { SystemHealth } = await import('./widgets/SystemHealth')
    render(
      <DashboardLiveProvider>
        <SystemHealth navigate={vi.fn()} sub="" navEpoch={0} query={{}} setQuery={() => {}} />
      </DashboardLiveProvider>,
    )
  }

  it('says the health is unknown when the probe cannot be read', async () => {
    mockApi({ doctor: boom })
    await mountStrip()
    const row = await waitFor(() => screen.getByTitle(/health probe could not be read/i))
    expect(row.textContent, 'names the state, not a fault we did not measure').toMatch(/Health unknown/)
    expect(screen.queryByText(/degraded|Core failing/), 'no invented fault').toBeNull()
  })

  it('stays quiet when the probe says everything is healthy', async () => {
    mockApi({})
    await mountStrip()
    await waitFor(() => expect(screen.queryByText(/Health unknown/)).toBeNull())
    expect(screen.queryByText(/degraded/)).toBeNull()
  })

  it('still reports a real degradation as a fault', async () => {
    mockApi({ doctor: () => Promise.resolve(sickReport) })
    await mountStrip()
    await waitFor(() => expect(screen.getByText(/degraded/)).toBeInTheDocument())
    expect(screen.queryByText(/Health unknown/), 'a measured fault is not "unknown"').toBeNull()
  })
})

describe('the settings health and safety cards say when they could not check', () => {
  const src = () => readFileSync(join(process.cwd(), "src/features/settings/settingsWidgets.tsx"), 'utf8')
  const code = () => src().replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  const registration = (c: string, key: string) => {
    const at = c.indexOf(`useQuery('${key}'`)
    expect(at, `${key} must be registered`).toBeGreaterThan(-1)
    let i = c.indexOf('(', at) + 1
    let depth = 1
    while (i < c.length && depth > 0) {
      if (c[i] === '(') depth++
      else if (c[i] === ')') depth--
      i++
    }
    return c.slice(at, i)
  }

  it('neither fetcher maps its rejection to a value that reads as "loaded"', () => {
    for (const key of ['settings:doctor', 'settings:incident']) {
      expect(registration(code(), key), `${key} must not swallow its rejection`).not.toMatch(/\.catch\(/)
    }
  })

  const cardBlock = (c: string, id: string) => {
    const at = c.indexOf(`id: '${id}', group:`)
    expect(at, `the ${id} card must exist`).toBeGreaterThan(-1)
    const open = c.lastIndexOf('{', at)
    let i = open
    let depth = 0
    do {
      if (c[i] === '{') depth++
      else if (c[i] === '}') depth--
      i++
    } while (i < c.length && depth > 0)
    return c.slice(open, i)
  }

  it('both cards render a "could not check" state and keep their loading flag honest', () => {
    const c = code()
    for (const id of ['doctor', 'guardrails']) {
      const block = cardBlock(c, id)
      expect(block, `the ${id} card must offer a could-not-check state`).toMatch(/Could ?n['’]t check/)
      expect(block, `the ${id} card's loading must exclude the failed case`)
        .toMatch(/=== undefined && !\w*Err/)
    }
  })

  it('the savings tile keeps its catch — a missing number really is "no data"', () => {
    expect(code()).toMatch(/useToolsSavings[\s\S]{0,120}?\.catch\(\(\) => null/)
  })
})

describe('the probe failure reaches the consumers at all', () => {
  it('DashboardLive publishes it instead of dropping it', () => {
    const src = readFileSync(join(process.cwd(), "src/features/dashboard/DashboardLive.tsx"), 'utf8')
    const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code, 'the loader keeps the rejection').toMatch(/\.catch\(\(e\) => guard\(setDoctorErr\)\(e\)\)/)
    expect(code, 'and the context carries it').toMatch(/doctor, doctorErr,/)
    expect(code, 'the old silent catch is gone').not.toMatch(/api\.doctor\(\)[\s\S]{0,80}?catch\(\(\) => \{\}\)/)
  })
})
