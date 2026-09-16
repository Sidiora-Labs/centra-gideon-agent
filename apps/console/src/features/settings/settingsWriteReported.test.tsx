import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const notified: string[] = []
function mockNotify() {
  notified.length = 0
  vi.doMock('../../app/shell/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string) => { notified.push(msg) },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('a refused speech setting rolls back and says so', () => {
  async function mountVoice(save: () => Promise<unknown>) {
    mockNotify()
    vi.doMock('../../shared/data/api', async (orig) => ({
      ...(await orig<Record<string, unknown>>()),
      api: {
        useCaseSettings: () => Promise.resolve({ enabled: false }),
        modelsActive: () => Promise.resolve({ stt: ['whisper:base'], tts: ['piper:en'] }),
        saveUseCaseSettings: save,
        gideonConfig: () => Promise.resolve({}),
        voiceLoopConfig: () => Promise.resolve({}),
        voiceProfiles: () => Promise.resolve({ profiles: [], bindings: {} }),
        voiceResolve: () => Promise.resolve({ surface: '', resolved: true, level: 'built-in' }),
      },
    }))
    const { VoicePanel } = await import('./VoicePanel')
    render(<VoicePanel go={() => {}} />)
  }

  it('does not keep a value the server refused', async () => {
    await mountVoice(() => Promise.reject(new Error('stt model unavailable')))
    const toggle = await waitFor(() => screen.getAllByRole('switch')[0])
    const before = toggle.getAttribute('aria-checked')
    fireEvent.click(toggle)
    await waitFor(() => expect(notified.some((m) => /speech setting/i.test(m))).toBe(true))
    await waitFor(() => expect(screen.getAllByRole('switch')[0].getAttribute('aria-checked')).toBe(before))
    expect(notified[0], "carries the server's own reason").toMatch(/stt model unavailable/)
  })

  it('keeps the new value and stays quiet when the save succeeds', async () => {
    await mountVoice(() => Promise.resolve({ ok: true }))
    const toggle = await waitFor(() => screen.getAllByRole('switch')[0])
    const before = toggle.getAttribute('aria-checked')
    fireEvent.click(toggle)
    await waitFor(() => expect(screen.getAllByRole('switch')[0].getAttribute('aria-checked')).not.toBe(before))
    expect(notified, 'a successful save says nothing').toEqual([])
  })
})

describe('the shared settings mutation reports as well as reconciles', () => {
  const SRC = join(process.cwd(), "src")
  const strip = (src: string) =>
    src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
  const codeOf = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

  it('mutate() notifies on rejection AND still invalidates on both paths', () => {
    const code = codeOf('features/settings/settingsWidgets.tsx')
    const at = code.indexOf('async function mutate(')
    expect(at, 'the helper must still exist').toBeGreaterThan(-1)
    const fn = code.slice(at, at + 600)
    expect(fn, 'the rejection must be reported').toMatch(/catch \(e\)[\s\S]{0,160}?notify\(/)
    expect(fn, 'and never swallowed silently again').not.toMatch(/catch \{\s*\}/)
    const notifyAt = fn.indexOf('notify(')
    const invalidateAt = fn.indexOf('invalidateSpecs(')
    expect(invalidateAt, 'invalidate must come AFTER the catch block, i.e. on both paths')
      .toBeGreaterThan(notifyAt)
  })

  it('every hub tile inherits it — the call sites still route through mutate', () => {
    const code = codeOf('features/settings/settingsWidgets.tsx')
    const sites = code.match(/\bmutate\(/g) ?? []
    expect(sites.length, 'call sites through the shared helper').toBeGreaterThanOrEqual(8)
  })

  it("the model repair reports, like the two siblings in its own file", () => {
    const code = codeOf('features/settings/ModelsPanel.tsx')
    const at = code.indexOf('const repair =')
    const fn = code.slice(at, at + 520)
    expect(fn, 'an unhandled rejection made a failed repair look like a dead click').toMatch(/catch \(e\)[\s\S]{0,200}?notify\(/)
    expect(fn, 'and the pending flag still clears on both paths').toMatch(/finally \{ setRepairing\(null\) \}/)
  })

  it('the deliberate optimists are still named, and still deliberate', () => {
    expect(codeOf('shared/ui/FeedbackThumbs.tsx'), 'still optimistic on purpose').toMatch(/catch \{/)
    expect(codeOf('features/settings/ProvidersPanel.tsx'), 'still keeps the last known runtimes')
      .toMatch(/setRuntimeOverride\(await api\.agentRuntimes\(true\)\)/)
  })

  it('NO source file anywhere in the tree still swallows a write into silence', () => {
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const e of readdirSync(dir, { withFileTypes: true })) {
        const abs = join(dir, e.name)
        if (e.isDirectory()) walk(abs, out)
        else if (/\.tsx?$/.test(e.name) && !/\.(test|doc|spec)\./.test(e.name)) out.push(abs)
      }
      return out
    }
    const WRITE = /await api\.(save|patch|set|start|delete|create|update)\w*\([\s\S]{0,200}?catch \{\s*\}/g

    const ALLOWED: Record<string, { n?: number; why: string }> = {
      'features/loops/DesignStepPreview.tsx  api.updateULoop': {
        why: 'mount-time auto-merge, not a user action; the preview loads regardless',
      },
      'features/terminal/TerminalView.tsx  api.createTerminal': {
        why: 'measured: the reconnect re-binds a working session, so the click still succeeds',
      },
    }

    const found: string[] = []
    const files = walk(SRC)
    for (const abs of files) {
      const rel = abs.replace(SRC + '/', '')
      const code = strip(readFileSync(abs, 'utf8'))
      for (const m of code.matchAll(WRITE)) {
        const method = /await (api\.\w+)\(/.exec(m[0])?.[1] ?? 'api.?'
        found.push(`${rel}  ${method}`)
      }
    }

    const surprises = [...new Set(found)].filter((k) => !(k in ALLOWED))
    expect(surprises, `a write whose failure reaches nobody:\n${surprises.join('\n')}`).toEqual([])

    const actual: Record<string, number> = {}
    for (const k of found) actual[k] = (actual[k] ?? 0) + 1
    const expected = Object.fromEntries(Object.entries(ALLOWED).map(([k, v]) => [k, v.n ?? 1]))
    expect(actual, 'the allowance must match the remainder exactly, count included').toEqual(expected)

    expect(files.length, 'vacuity floor: the whole tree must have been scanned').toBeGreaterThan(400)
    expect(
      files.some((f) => f.includes('features/settings/')) && files.some((f) => f.includes('features/knowledge/')),
      'the walk must reach outside pages/settings — that scope was the original defect',
    ).toBe(true)
  })

  it('the two writes this sweep found outside settings now report', () => {
    const shelf = codeOf('features/knowledge/KnowledgeListPage.tsx')
    expect(shelf, 'the create-shelf call reports').toMatch(
      /createKnowledgeCollection\([\s\S]{0,140}?reportActionFailure\(/)
    expect(shelf, 'and its follow-ups are gated on the result').toMatch(/if \(!res\) return/)

    const preview = codeOf('features/loops/DesignStepPreview.tsx')
    expect(preview, 'the user token edit reports').toMatch(/reportingWrite\(`save the \$\{path\} override`/)
    expect(preview, 'and its refetch is gated').toMatch(/if \(ok\) await loadTokens\(\)/)
  })
})
