import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

describe('the agents meta line cannot strand its separator', () => {
  const src = read('features/agents/AgentsListPage.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/function NativeRow\(/)
    expect(src).toMatch(/function DiscoveredRow\(/)
    expect(src.length).toBeGreaterThan(4000)
  })

  it('gates the dot on the model that it separates from', () => {
    expect(src, 'the model is optional, so the separator must be too').toMatch(
      /\{agent\.model \? '· ' : ''\}\{agent\.description\}/,
    )
  })

  it('no longer hard-codes the dot onto the description', () => {
    expect(/>· \{agent\.description\}/.test(src), 'a literal prefix reappeared').toBe(false)
  })

  it('the sibling row still renders its description bare — the two variants must agree', () => {
    const discovered = src.slice(src.indexOf('function DiscoveredRow('))
    expect(/·/.test(discovered), 'DiscoveredRow has no model, so it can never earn a separator').toBe(false)
  })
})

describe('the knowledge summary cannot strand its separator', () => {
  const src = read('features/knowledge/KnowledgeListPage.tsx')

  it('reads the real file (not vacuously green)', () => {
    expect(src).toMatch(/truncate/)
    expect(src).toMatch(/it\.summary \|\| it\.content/)
    expect(src.length).toBeGreaterThan(4000)
  })

  it('the summary renders bare — it always wraps to its own line', () => {
    expect(src, 'the summary must not carry a leading separator').toMatch(
      /\{\(it\.summary \|\| it\.content\) && <span className="truncate">\{it\.summary \|\| it\.content\}<\/span>\}/,
    )
  })

  it('the exact pre-fix shape does not come back', () => {
    expect(/<span className="truncate">· \{it\.summary/.test(src), 'the dangling prefix reappeared').toBe(false)
  })

  it('the short file_size sibling KEEPS its separator — it shares the label line', () => {
    expect(src).toMatch(/<span>· \{fmtBytes\(it\.file_size\)\}<\/span>/)
  })
})

describe('the canonical form on #/tasks is now NO GLYPH — the computed one was measured unsafe', () => {
  const src = read('features/tasks/TasksListPage.tsx')

  it('MetaLine carries no separator glyph at all — computed or literal', () => {
    const at = src.indexOf('function MetaLine(')
    expect(at, 'MetaLine must still exist').toBeGreaterThan(-1)
    const body = src
      .slice(at, src.indexOf('\nfunction ', at + 1))
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    expect(
      ['·', '•', '–'].filter((g) => body.includes(g)),
      'a separator glyph is back. Computing it does not stop it stranding — 4 of 4 rows stranded at ' +
        '320px with the conditional in place. Separate by `gap-x-*`.',
    ).toEqual([])
    expect(body, 'and the gap that replaced it must stay').toMatch(/gap-x-m\b/)
  })
})
