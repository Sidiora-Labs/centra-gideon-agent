import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const boom = () => Promise.reject(new Error('provider registry unavailable'))

function mockApi(over: Record<string, unknown>) {
  const named: Record<string, unknown> = {
    actionProviders: () => Promise.resolve([
      { name: 'run_prompt', display_name: 'Run a prompt', supports_blocking: false, settingsSchema: {} },
    ]),
    triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }),
    appEvents: () => Promise.resolve([]),
    prompts: () => Promise.resolve([]),
    ...over,
  }
  const api = new Proxy(named, {
    get(target, prop: string) {
      if (prop in target) return target[prop]
      return () => Promise.resolve([])
    },
  })
  vi.doMock('../../shared/data/api', async (orig) => ({ ...(await orig<Record<string, unknown>>()), api }))
}

async function mount() {
  const { TriggerCreatePage } = await import('./TriggerCreatePage')
  render(<TriggerCreatePage onBack={() => {}} onCreated={() => {}} query={{}} setQuery={() => {}} />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('#/triggers/new says its action list failed instead of blaming the user', () => {
  it('reports the failed read in the Action field, with a retry', async () => {
    mockApi({ actionProviders: boom })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed').toMatch(/could ?n.t load the action providers/i)
    expect(alert.textContent, "carries the server's reason").toMatch(/provider registry unavailable/)
    expect(screen.getByRole('button', { name: /Retry/ }), 'and offers a way out').toBeInTheDocument()
  })

  it('stops telling the user to pick a provider that cannot be listed', async () => {
    mockApi({ actionProviders: boom })
    await mount()
    fireEvent.change(screen.getByPlaceholderText('Morning briefing'), { target: { value: 'nightly digest' } })
    const save = await waitFor(() => screen.getByRole('button', { name: /Create trigger/i }))
    await waitFor(() => {
      expect(save.getAttribute('title'), 'the reason must name the failed read, not a choice')
        .toMatch(/could ?n.t load the action providers/i)
    })
    expect(save.getAttribute('title'), 'never the old blame').not.toMatch(/^Pick a provider/)
  })

  it('still offers the picker — and no error — when the registry really is empty', async () => {
    mockApi({ actionProviders: () => Promise.resolve([]) })
    await mount()
    await waitFor(() => expect(screen.getByText(/Pick an action/i)).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'an empty registry is not a failure').toBeNull()
  })

  it('renders the picker normally when the read succeeds', async () => {
    mockApi({})
    await mount()
    await waitFor(() => expect(screen.getByText(/Pick an action/i)).toBeInTheDocument())
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('the create paths keep their failures visible', () => {
  const SRC = join(process.cwd(), "src")
  const codeOf = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the providers read no longer substitutes an empty list', () => {
    const code = codeOf('features/triggers/TriggerCreatePage.tsx')
    const at = code.indexOf("useQuery('triggers:action-providers'")
    expect(at, 'the read must still be here').toBeGreaterThan(-1)
    let i = code.indexOf('(', at) + 1
    let depth = 1
    while (i < code.length && depth > 0) {
      if (code[i] === '(') depth++
      else if (code[i] === ')') depth--
      i++
    }
    expect(code.slice(at, i), 'no fallback list').not.toMatch(/\.catch\(/)
  })

  it("the ingest path's title/tags patch reports instead of vanishing", () => {
    const code = codeOf('features/knowledge/KnowledgeCreatePage.tsx')
    const at = code.indexOf('updateKnowledge(res.item_id, custom)')
    expect(at, 'the patch must still be here').toBeGreaterThan(-1)
    const seg = code.slice(at, at + 320)
    expect(seg, 'the rejection must not be discarded').not.toMatch(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/)
    expect(seg, 'and must say what was and was not saved').toMatch(/notify\([^)]*Saved the file/)
  })

  it('InlineError gained the retry as an OPTIONAL prop — existing callers are untouched', () => {
    const code = codeOf('shared/ui/InlineError.tsx')
    expect(code).toMatch(/onRetry\?:\s*\(\) => void/)
    expect(code, 'and it renders only when passed').toMatch(/\{onRetry && \(/)
    expect(code, 'onRetry must not be a required prop').not.toMatch(/onRetry:\s*\(\) => void/)
  })

  it('the tag SUGGESTIONS read keeps its fallback, deliberately', () => {
    const code = codeOf('features/knowledge/KnowledgeCreatePage.tsx')
    expect(code).toMatch(/knowledgeTags\(\)\.catch\(\(\) => \[\]/)
  })
})
