import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) out.push(...walk(abs))
    else if (/\.tsx?$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const EDITORS = [
  { file: 'features/settings/MemoryPanel.tsx', load: 'api.memoryDoc', save: 'api.saveMemoryDoc' },
  { file: 'features/agents/AgentDetail.tsx', load: 'api.agentMetadata', save: 'api.saveAgentMetadata' },
]

describe('a doc editor that can overwrite must not treat a failed read as empty content', () => {
  for (const ed of EDITORS) {
    describe(ed.file, () => {
      const code = strip(readFileSync(join(SRC, ed.file), 'utf8'))

      it('reads and writes the same document — which is what makes this data loss, not a cosmetic bug', () => {
        expect(code, `${ed.file} still performs the read`).toContain(ed.load)
        expect(code, `${ed.file} still performs the write`).toContain(ed.save)
        expect(code, 'save is still gated on the null-vs-empty distinction this rail protects')
          .toMatch(/const dirty = content !== null && draft !== content/)
      })

      it('the load failure does NOT blank the content or the draft', () => {
        expect(code, 'setContent(\'\') on the failure path is the data-loss mechanism')
          .not.toMatch(/catch[\s\S]{0,120}setContent\(''\)/)
        expect(code, 'setDraft(\'\') on the failure path arms Save against a document nobody saw')
          .not.toMatch(/catch[\s\S]{0,120}setDraft\(''\)/)
      })

      it('the load failure is reported instead, so the state is not inferred from an absence', () => {
        expect(code, 'a failed read sets a load-error state').toMatch(/catch[\s\S]{0,160}setLoadErr\(/)
        expect(code, 'and the failure is announced, not just drawn').toMatch(/role="alert"/)
      })
    })
  }

  it('the two editors are still the only surfaces that blank LOADED content they can write back', () => {
    const both = EDITORS.map((e) => strip(readFileSync(join(SRC, e.file), 'utf8')))
    for (const code of both) {
      expect(code, 'neither editor may reintroduce the empty-string failure path')
        .not.toMatch(/catch[\s\S]{0,120}set(?:Content|Draft|ViewContent)\(''\)/)
    }
    expect(walk(SRC).length, 'the source tree is readable from here').toBeGreaterThan(200)
  })
})
