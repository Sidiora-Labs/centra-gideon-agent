import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const DISCLOSURES: [string, string, string][] = [
  ['features/chat/ContextLedger.tsx', 'open', 'aria-expanded={open}\n        data-type="caption"\n        className="flex items-center gap-1.5 rounded-pill'],
  ['features/artifacts/ArtifactViewer.tsx', 'metaOpen', 'setMetaOpen((v) => !v)} aria-expanded={metaOpen}'],
  ['features/loops/CockpitPromptBar.tsx', 'open', 'aria-expanded={open} className="flex w-full items-center gap-s text-left min-w-0"'],
  ['features/loops/LoopCockpitPage.tsx', 'promptOpen', 'setPromptOpen(!promptOpen)} aria-expanded={promptOpen}'],
  ['features/loops/LoopCockpitPage.tsx', 'open', 'aria-expanded={open} className="w-full flex items-center gap-s px-m py-2 text-left"'],
  ['features/settings/AuditPanel.tsx', 'open', 'aria-expanded={open} data-type="caption" className="flex w-full items-center gap-2 text-left'],
  ['features/settings/MemoryPanel.tsx', 'open', 'aria-expanded={open} className="w-full text-left"'],
  ['shared/ui/DisclosureCard.tsx', 'open', 'aria-expanded={open} aria-controls={bodyId}\n        className="flex w-full items-center gap-3 px-4 py-3 text-left'],
  ['features/tools/ToolOutput.tsx', 'open', 'aria-expanded={open} className="inline-flex items-center gap-1 text-on-surface-var'],
]

describe('a raw disclosure button announces its state', () => {
  for (const [rel, state, anchor] of DISCLOSURES) {
    it(`${rel}${state === 'promptOpen' ? ' (prompt)' : ''} announces ${state}`, () => {
      expect(read(rel), `${rel} must carry the attribute on this specific button`).toContain(anchor)
    })

    it(`${rel} still gates content on ${state}`, () => {
      const src = read(rel)
      expect(src.includes(`{${state} && `) || src.includes(`${state} ?`), `${rel} must render on ${state}`).toBe(true)
    })
  }
})

describe('a mode toggle gets pressed — unless its name already says so', () => {
  it('autoscroll is pressed, because its title names the state and the rest is a tint', () => {
    expect(read('features/settings/DiagnosticsPanel.tsx')).toContain('aria-pressed={autoscroll}')
  })

  it('pause stays silent, because its title names the next action', () => {
    const src = read('features/settings/DiagnosticsPanel.tsx')
    const at = src.indexOf('setPaused((v) => !v)')
    expect(at).toBeGreaterThan(-1)
    expect(src.slice(at, at + 200), 'a name that flips needs no second channel').not.toMatch(/aria-pressed|aria-expanded/)
    expect(src.slice(at, at + 200)).toMatch(/title=\{paused \? 'Resume' : 'Pause'\}/)
  })

  it("PromptDetail's raw/rendered switch stays silent for the same reason", () => {
    const src = read('features/prompts/PromptDetail.tsx')
    const at = src.indexOf('setRaw((r) => !r)')
    expect(at).toBeGreaterThan(-1)
    expect(src.slice(at, at + 260)).not.toMatch(/aria-pressed|aria-expanded/)
    expect(src.slice(at, at + 260), 'its LABEL flips too, not just the title').toMatch(/Rendered|Raw/)
  })
})

describe('the census ceiling falls', () => {
  it('67 toggles across src, at most 20 silent', () => {
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const TOGGLE = /onClick=\{\(\) => set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => set\w+\(!\w+\)/g
    const elementAround = (src: string, at: number): string => {
      const open = src.lastIndexOf('<', at)
      const start = open === -1 ? Math.max(0, at - 340) : open
      let depth = 0
      for (let i = start; i < src.length; i++) {
        const c = src[i]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) return src.slice(start, i + 1)
      }
      return src.slice(start, Math.min(src.length, at + 380))
    }
    const ANNOUNCES = /aria-expanded|aria-pressed|ariaExpanded|ariaPressed|\bon=\{|\bactive=\{/
    const flipsOnItsOwnState = (el: string, setter: string): boolean => {
      const state = setter.replace(/^set/, '')
      const lower = state.charAt(0).toLowerCase() + state.slice(1)
      const named = new RegExp(`(?:label|title)=\\{[^}]*\\b(?:${lower}|${state})\\b[^}]*\\?`)
      return named.test(el)
    }
    const found = walk(SRC).flatMap((abs) => {
      const src = readFileSync(abs, 'utf8')
      return [...src.matchAll(TOGGLE)].map((m) => ({
        el: elementAround(src, m.index!),
        setter: (m[0].match(/set\w+/) ?? ['set'])[0],
        where: `${abs.slice(SRC.length + 1)}:${src.slice(0, m.index!).split('\n').length}`,
      }))
    })
    expect(found.length, 'the population must still be found').toBeGreaterThanOrEqual(60)
    const silent = found.filter(
      (f) => !ANNOUNCES.test(f.el) && !flipsOnItsOwnState(f.el, f.setter),
    )
    expect(
      silent.map((f) => f.where),
      'a toggle that announces neither state nor a flipping name — announce it, or flip its label',
    ).toEqual([])
  })
})
