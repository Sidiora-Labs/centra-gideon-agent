import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'


const SYSTEM = {
  platform: 'darwin', hostname: 'mb', os: 'Darwin', python: '3.12', arch: 'arm64', pid: 1, cwd: '/',
  cpu_count: 18, cpu_pct: 22, mem_total_gb: 48, mem_used_gb: 31.8, mem_free_gb: 16.2, proc_mem_mb: 120,
  load_1m: 6.07, load_5m: 5, load_15m: 4, disk_total_gb: 926, disk_free_gb: 623,
  net_rx_kbs: 9728, net_tx_kbs: 10956,
}
const STATUS = {
  uptime: '5m 46s', version: '0.1.3', platform: 'darwin', triggers: 4, subagents: 0,
  update_available: false,
}

function mockApi() {
  vi.doMock('../../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      status: () => Promise.resolve(STATUS),
      system: () => Promise.resolve(SYSTEM),
      doctor: () => Promise.resolve({ ok: true, core_ok: true, worst: '', capabilities: {} }),
      notifications: () => Promise.resolve({ notifications: [] }),
      discover: () => Promise.resolve({ tips: [] }),
      approvals: () => Promise.resolve([]),
      inboxOpen: () => Promise.resolve([]),
      skillProposals: () => Promise.resolve({ proposals: [], lastReview: null }),
      uLoops: () => Promise.resolve([]),
      readyTasks: () => Promise.resolve([]),
      triggersHistory: () => Promise.resolve({ entries: [] }),
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

function metrics(): HTMLElement[] {
  const values = [...document.querySelectorAll<HTMLElement>('span[data-type="title-m"]')]
  return values
    .map((v) => v.parentElement as HTMLElement)
    .filter((d) => d && d.querySelector(':scope > svg') && d.querySelector(':scope > span[data-type="title-m"]'))
}

const RESPONSIVE = /(?:^|\s)(?:@(?:min-\[[^\]]+\]|max-\[[^\]]+\]|[a-z0-9]+)|(?:max-)?(?:sm|md|lg|xl|2xl)):/

async function mountStrip() {
  const { DashboardLiveProvider } = await import('../DashboardLive')
  const { SystemHealth } = await import('./SystemHealth')
  render(
    <DashboardLiveProvider>
      <SystemHealth navigate={vi.fn()} sub="" navEpoch={0} query={{}} setQuery={() => {}} />
    </DashboardLiveProvider>,
  )
  await waitFor(() => expect(metrics().length).toBe(9))
  return metrics()
}

describe('every dashboard metric says what it is measuring, at every width', () => {
  beforeEach(mockApi)

  it('renders nine labelled metrics — the positive control for everything below', async () => {
    const found = await mountStrip()
    expect(found.length, 'nine metrics must render on a full /api/system payload').toBe(9)
    const labels = found.map((m) => m.querySelector('span[data-type="body-m"]')?.textContent)
    expect(labels).toEqual([
      'uptime', 'darwin', 'cpu', 'mem', 'net', 'disk', 'load · 18cpu', 'triggers', 'subagents',
    ])
  })

  it('gates no label behind a width — this is the assertion that fails on the old build', async () => {
    const found = await mountStrip()
    for (const m of found) {
      const label = m.querySelector<HTMLElement>('span[data-type="body-m"]')!
      const classes = label.className.split(/\s+/).filter(Boolean)
      expect(classes, `"${label.textContent}" must not be display:none by default`).not.toContain('hidden')
      expect(classes.filter((c) => RESPONSIVE.test(c)),
        `"${label.textContent}" must not be gated on a container or viewport width`).toEqual([])
    }
  })

  it('keeps no title tooltip standing in for the label it no longer hides', async () => {
    const found = await mountStrip()
    for (const m of found) {
      expect(m.getAttribute('title'), `${m.textContent} must not re-state itself in a tooltip`).toBeNull()
      expect(m.getAttribute('aria-label'), 'the visible text is the whole name').toBeNull()
    }
  })

  it('keeps the wrap escape hatch that makes always-on labels safe', async () => {
    const found = await mountStrip()
    const strip = found[0].closest('[class*="flex-wrap"]')
    expect(strip, 'the metric strip must stay a wrapping flex row').not.toBeNull()
    expect(found.every((m) => m.className.split(/\s+/).includes('shrink-0')),
      'and each metric stays unsqueezed so the wrap, not the word, gives way').toBe(true)
  })
})
