import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const notified: string[] = []
function mockNotify() {
  notified.length = 0
  vi.doMock('./appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (m: string) => { notified.push(m) },
  }))
}

const OPAQUE = ['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.', 'HTTP 500']

beforeEach(() => { vi.resetModules() })

describe('a toast never ends in the browser’s debug string', () => {
  it('suppresses every opaque message and closes the sentence with a full stop', async () => {
    mockNotify()
    const { reportingWrite } = await import('./reportingWrite')
    for (const m of OPAQUE) {
      notified.length = 0
      const ok = await reportingWrite('save that', () => Promise.reject(new Error(m)))
      expect(ok, 'the write still reports failure').toBe(false)
      expect(notified).toEqual(["Couldn't save that."])
      expect(notified[0], 'no debug string').not.toContain(m)
      expect(notified[0], 'and no dangling colon').not.toMatch(/:\s*$/)
    }
  })

  it('an AUTHORED backend message still survives — or this rail is a mute button', async () => {
    mockNotify()
    const { reportingWrite } = await import('./reportingWrite')
    await reportingWrite('save that', () => Promise.reject(new Error('name is required')))
    expect(notified).toEqual(["Couldn't save that: name is required"])
  })

  it('reportActionFailure behaves identically — both forms, one sentence', async () => {
    mockNotify()
    const { reportActionFailure } = await import('./reportingWrite')
    reportActionFailure('refresh this tile')(new Error('Failed to fetch'))
    reportActionFailure('refresh this tile')(new Error('quota exceeded'))
    expect(notified).toEqual(["Couldn't refresh this tile.", "Couldn't refresh this tile: quota exceeded"])
  })

  it('a non-Error rejection does not print "[object Object]" or an empty tail', async () => {
    mockNotify()
    const { reportingWrite } = await import('./reportingWrite')
    await reportingWrite('do that', () => Promise.reject({ weird: true }))
    expect(notified[0]).toBe("Couldn't do that.")
    expect(notified[0]).not.toContain('object')
  })
})

describe('the full-page boundary does not show a minified exception', () => {
  async function boundaryWith(msg: string) {
    const { ErrorBoundary } = await import('./ErrorBoundary')
    const { PersonalityProvider } = await import('./personality')
    const Boom = () => { throw new Error(msg) }
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(<PersonalityProvider><ErrorBoundary><Boom /></ErrorBoundary></PersonalityProvider>)
    err.mockRestore()
  }

  beforeEach(() => {
    vi.doMock('./appearance', () => ({ useAppearance: () => ({ applyScheme: () => {}, setSelect: () => {} }) }))
  })

  it('falls back to its OWN written sentence for an opaque message', async () => {
    await boundaryWith('Failed to fetch')
    expect(screen.getByText('Something went wrong rendering this view.')).toBeInTheDocument()
    expect(screen.queryByText('Failed to fetch'), 'the debug string must not be the page').toBeNull()
  })

  it('still shows an authored message — including the frozen fixture’s', async () => {
    await boundaryWith('kaboom')
    expect(screen.getByText('kaboom')).toBeInTheDocument()
    expect(screen.queryByText('Something went wrong rendering this view.')).toBeNull()
  })
})

describe('the sentence has ONE owner, and the filter is really adopted', () => {
  const SRC = join(process.cwd(), "src")
  const strip = (s: string) => s
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^(\s*)\/\/.*$/gm, '$1')
  const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) ? [p] : []
  })
  const codeOf = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

  it('both exports compose through the same helper, so they cannot drift', () => {
    const code = codeOf('app/shell/reportingWrite.ts')
    expect([...code.matchAll(/notify\(failureSentence\(what, error\), 'error'\)/g)].length,
      'one notifier owns the composed failure sentence').toBe(1)
    expect(code).toContain('reportActionFailure(what)(error)')
    expect([...code.matchAll(/function failureSentence/g)].length, 'defined exactly once').toBe(1)
    expect(code, 'and the raw idiom is gone from the shared path').not.toMatch(/instanceof Error \? \w+\.message/)
  })

  it('the shared error path routes through readableErrText', () => {
    for (const rel of ['app/shell/reportingWrite.ts', 'app/shell/ErrorBoundary.tsx']) {
      expect(codeOf(rel), `${rel} must use the filter`).toMatch(/readableErrText\(/)
    }
  })

  it('VACUITY: readableErrText has more than its original single consumer', () => {
    const consumers = walk(SRC).filter((abs) => !/\.(test|doc)\.tsx?$/.test(abs))
      .filter((abs) => {
        const rel = abs.slice(SRC.length + 1)
        if (rel === 'shared/data/errText.ts') return false
        return /readableErrText\s*\(/.test(strip(readFileSync(abs, 'utf8')))
      })
    expect(consumers.length, 'production consumers of readableErrText').toBeGreaterThanOrEqual(3)
  })
})
