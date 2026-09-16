import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const config = { schema: { properties: { room: { type: 'string' } } }, config: { room: 'general' }, _secret_set: [] }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      appConfig: () => Promise.resolve(config),
      saveAppConfig: vi.fn(() => Promise.resolve({ ok: true })),
      ...over,
    },
  }))
}

async function mountProbe() {
  const { useAppConfig } = await import('./appConfigForm')
  const seen: { loading: boolean; error: unknown; cur: Record<string, unknown> }[] = []
  function Probe() {
    const cfg = useAppConfig('slack-channel') as ReturnType<typeof useAppConfig> & { error?: unknown }
    seen.push({ loading: cfg.loading, error: cfg.error, cur: cfg.cur })
    return (
      <div>
        <span data-testid="loading">{String(cfg.loading)}</span>
        <span data-testid="error">{cfg.error ? 'yes' : 'no'}</span>
        <span data-testid="cur">{JSON.stringify(cfg.cur)}</span>
        <button onClick={() => cfg.save()}>save</button>
        <span data-testid="err">{cfg.err ?? ''}</span>
      </div>
    )
  }
  render(<Probe />)
  return { seen }
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('a failed app-config read is reported, not hidden behind "Loading…"', () => {
  it('surfaces the error instead of loading forever', async () => {
    mockApi({ appConfig: () => Promise.reject(new Error('gateway down')) })
    await mountProbe()
    await waitFor(() => expect(screen.getByTestId('error').textContent).toBe('yes'))
    expect(screen.getByTestId('loading').textContent, 'a failed read is not still loading').toBe('false')
  })

  it('reports the load failure to the user when a save is attempted anyway', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi({ appConfig: () => Promise.reject(new Error('gateway down')), saveAppConfig })
    await mountProbe()
    await waitFor(() => expect(screen.getByTestId('error').textContent).toBe('yes'))
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(screen.getByTestId('err').textContent).toMatch(/could ?n[o']t|failed|not loaded/i))
    expect(saveAppConfig, 'an unloaded form must not write').not.toHaveBeenCalled()
  })

  it('refuses the same write DURING the normal load window — no failure required', async () => {
    const saveAppConfig = vi.fn(() => Promise.resolve({ ok: true }))
    mockApi({ appConfig: () => new Promise(() => {}), saveAppConfig })
    await mountProbe()
    expect(screen.getByTestId('loading').textContent).toBe('true')
    expect(JSON.parse(screen.getByTestId('cur').textContent!), 'nothing has loaded yet').toEqual({})
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(saveAppConfig).not.toHaveBeenCalled())
  })

  it('still saves normally once the config has loaded', async () => {
    const saveAppConfig = vi.fn((_name: string, _cfg: Record<string, unknown>) => Promise.resolve({ ok: true }))
    mockApi({ saveAppConfig })
    await mountProbe()
    await waitFor(() => expect(screen.getByTestId('loading').textContent).toBe('false'))
    expect(JSON.parse(screen.getByTestId('cur').textContent!)).toEqual({ room: 'general' })
    fireEvent.click(screen.getByRole('button', { name: 'save' }))
    await waitFor(() => expect(saveAppConfig).toHaveBeenCalledTimes(1))
    expect(saveAppConfig.mock.calls[0][1], 'the loaded values, not an empty object').toEqual({ room: 'general' })
  })
})

describe('the Save affordance matches the guard', () => {
  const componentBody = (src: string, name: string) => {
    const start = src.indexOf(`function ${name}(`)
    expect(start, `${name} must exist`).toBeGreaterThan(-1)
    let i = src.indexOf('(', start)
    let parens = 1
    i++
    while (i < src.length && parens > 0) {
      if (src[i] === '(') parens++
      else if (src[i] === ')') parens--
      i++
    }
    i = src.indexOf('{', i)
    let depth = 1
    const from = ++i
    while (i < src.length && depth > 0) {
      if (src[i] === '{') depth++
      else if (src[i] === '}') depth--
      i++
    }
    return src.slice(from, i - 1)
  }
  const buttonNamed = (body: string, label: string) => {
    const idx = body.indexOf(`>${label}</Button>`)
    expect(idx, `a Button labelled ${label} must exist in this component`).toBeGreaterThan(-1)
    const open = body.lastIndexOf('<Button', idx)
    return body.slice(open, idx + 1)
  }

  it('the Configure modal cannot offer Save before the config it would replace has loaded', () => {
    const src = readFileSync(join(process.cwd(), "src/features/apps/AppsSection.tsx"), 'utf8')
    const save = buttonNamed(componentBody(src, 'ConfigModal'), 'Save')
    const disabled = /disabled=\{([^}]*(?:\{[^}]*\})?[^}]*)\}/.exec(save)?.[1] ?? ''
    expect(disabled, 'the disabled expression must exist').not.toBe('')
    expect(disabled, 'gated on the read being in flight').toContain('cfg.loading')
    expect(disabled, 'and on the read having failed').toContain('cfg.error')
    expect(save, 'and it says why it is off').toMatch(/disabledReason=/)
  })

  it('the failed read renders a retry, not an eternal "Loading…"', () => {
    const src = readFileSync(join(process.cwd(), "src/features/apps/AppsSection.tsx"), 'utf8')
    expect(src).toMatch(/cfg\.error \?[\s\S]{0,400}?<LoadError what="app configuration"[^>]*onRetry=\{cfg\.reload\}/)
  })

  it('the settings panel answers the same failure the same way', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/AppsPanel.tsx"), 'utf8')
    expect(src, 'one family, one form').toMatch(/cfg\.error \?[\s\S]{0,400}?<LoadError what="app configuration"/)
  })
})
