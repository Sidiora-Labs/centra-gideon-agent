import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const SKELETON = /<(?:List|Form|CardGrid)Skeleton\b[^>]*?\/>/
const ERR_NOUN = /<(?:LoadError|InlineError)[^>]*?what=(?:"([^"]+)"|\{([^}]+?)\})/
const RESULTS = /results=\{\{([^}]*)\}\}/
const EMPTY_TITLE = /title=(?:"(No [^"]{2,40})"|\{[^}]*'(No [^']{2,40})')|(?:>|^)\s*(No [a-z][a-z' &-]{2,38})/
const GATE = /!?([A-Za-z_$][\w$]*)\s*(?:===|!==|&&|\?)/g

function blocks(lines: string[]): { name: string; start: number; end: number }[] {
  const starts: { name: string; start: number }[] = []
  lines.forEach((l, i) => {
    const m = /^(?:export\s+)?(?:default\s+)?function\s+(\w+)/.exec(l)
      || /^(?:export\s+)?const\s+(\w+)\s*[:=].*=>/.exec(l)
    if (m) starts.push({ name: m[1], start: i })
  })
  return starts.map((s, k) => ({ ...s, end: k + 1 < starts.length ? starts[k + 1].start : lines.length }))
}
const ownerOf = (bs: ReturnType<typeof blocks>, i: number) => bs.find((b) => i >= b.start && i < b.end)?.name ?? '?'

function gateIdents(lines: string[], i: number, before: string): string[] {
  const out = new Set<string>()
  const add = (text: string) => { for (const m of text.matchAll(GATE)) out.add(m[1]) }
  add(before)
  for (let d = 1; d <= 4 && out.size === 0; d++) {
    const l = lines[i - d]
    if (l !== undefined && /[?]|if \(/.test(l)) add(l)
  }
  return [...out]
}

type Site = {
  rel: string; line: number; tag: string; owner: string
  errNoun: string | null; errDist: number | null
  resultsNoun: string | null
  emptyTitle: string | null
}

function skeletons(): Site[] {
  const out: Site[] = []
  for (const abs of walk(SRC)) {
    if (abs.endsWith('ListScaffold.tsx')) continue
    const lines = readFileSync(abs, 'utf8').split('\n')
    const bs = blocks(lines)
    const declared = lines.flatMap((l, i) => {
      const inner = RESULTS.exec(l)?.[1]
      if (!inner) return []
      const noun = /noun:\s*([^,]+?)\s*(?:,|$)/.exec(inner)?.[1]
      const count = /count:\s*(.+?),\s*noun:/.exec(inner)?.[1]
      return noun && count ? [{ owner: ownerOf(bs, i), noun: noun.trim(), count }] : []
    })
    lines.forEach((text, i) => {
      const tag = SKELETON.exec(text)?.[0]
      if (!tag) return
      const owner = ownerOf(bs, i)
      let errNoun: string | null = null
      let errDist: number | null = null
      for (let d = 0; d <= 40 && errNoun === null; d++) {
        for (const j of [i - d, i + d]) {
          const m = lines[j] !== undefined ? ERR_NOUN.exec(lines[j]) : null
          if (m) { errNoun = (m[1] ?? m[2]).trim(); errDist = j - i; break }
        }
      }
      const idents = gateIdents(lines, i, text.slice(0, text.indexOf(tag)))
      const hit = declared.find((d) => d.owner === owner && idents.some((id) => new RegExp(`\\b${id}\\b`).test(d.count)))
      let emptyTitle: string | null = null
      for (let d = 1; d <= 12 && emptyTitle === null; d++) {
        const l = lines[i + d]
        if (l === undefined || ownerOf(bs, i + d) !== owner) break
        const m = EMPTY_TITLE.exec(l)
        if (m) emptyTitle = (m[1] ?? m[2] ?? m[3]).trim()
      }
      out.push({ rel: abs.slice(SRC.length + 1), line: i + 1, tag, owner, errNoun, errDist, resultsNoun: hit?.noun ?? null, emptyTitle })
    })
  }
  return out
}

const carries = (tag: string, noun: string) => {
  const lit = /^(['"])(.*)\1$/.exec(noun)
  const forms = lit ? [`what="${lit[2]}"`] : [`what="${noun}"`, `what={${noun}}`]
  return forms.some((f) => tag.includes(f))
}

const FROM_EMPTY_STATE: [string, string][] = [
  ['features/tasks/TasksListPage.tsx', 'tasks'],
  ['features/tools/ToolsPage.tsx', 'tools'],
  ['features/settings/ProjectionRulesPanel.tsx', 'custom rules'],
  ['features/settings/MemoryPanel.tsx', 'daily digests'],
  ['features/knowledge/TagManager.tsx', 'tags'],
  ['features/knowledge/ConflictPanel.tsx', 'contradictions'],
  ['features/skills/SkillProposals.tsx', 'skill proposals'],
]

const EXCLUDED: [string, string][] = [
  ['features/knowledge/KnowledgeListPage.tsx', '"No items reference this entity" is a sentence, and EntityDetail\'s own gate'],
]

const carriesFromEmpty = (tag: string, emptyTitle: string) => {
  const what = /what="([^"]+)"/.exec(tag)?.[1]
  if (!what) return false
  return emptyTitle.toLowerCase().includes(what.toLowerCase())
}

describe('a skeleton borrows a noun its own surface already declares', () => {
  const all = skeletons()
  const errPaired = all.filter((s) => s.errNoun)
  const resPaired = all.filter((s) => !s.errNoun && s.resultsNoun)

  it('finds the population — 57+ skeletons, 22+ beside a LoadError, the results bucket now drained', () => {
    expect(all.length, 'skeleton call sites outside the primitive').toBeGreaterThanOrEqual(57)
    expect(errPaired.length, 'skeletons with a LoadError noun in reach').toBeGreaterThanOrEqual(22)
    expect(errPaired.length + resPaired.length, 'skeletons with a sibling noun in reach')
      .toBeGreaterThanOrEqual(25)
  })

  it('every skeleton beside a LoadError passes that sibling noun', () => {
    const wrong = errPaired.filter((s) => !carries(s.tag, s.errNoun!))
      .map((s) => `${s.rel}:${s.line} should say what for ${s.errNoun} — ${s.tag}`)
    expect(wrong, `these name the failure but not the wait:\n${wrong.join('\n')}`).toEqual([])
  })

  it('every skeleton gated on counted rows passes the results noun', () => {
    const wrong = resPaired.filter((s) => !carries(s.tag, s.resultsNoun!))
      .map((s) => `${s.rel}:${s.line} should say what for ${s.resultsNoun} — ${s.tag}`)
    expect(wrong, `these count the rows by name but load them anonymously:\n${wrong.join('\n')}`).toEqual([])
  })

  it('the dynamic-noun form is visible to the scan — the blind spot that hid #/prompts', () => {
    const dyn = all.find((s) => s.rel === 'features/prompts/PromptsListPage.tsx')
    expect(dyn?.errNoun, 'the LoadError one line above declares the noun as an expression')
      .toBe("isSnips ? 'snippets' : 'prompts'")
    expect(dyn && carries(dyn.tag, dyn.errNoun!), 'and the skeleton passes the same expression').toBe(true)
  })

  it('every noun taken from an empty state still matches the copy users read', () => {
    for (const [rel, noun] of FROM_EMPTY_STATE) {
      const named = all.filter((s) => s.rel === rel && s.tag.includes(`what="${noun}"`))
      expect(named.length, `${rel} should still name a skeleton "${noun}"`).toBeGreaterThanOrEqual(1)
      const drifted = named.filter((s) => !s.emptyTitle || !carriesFromEmpty(s.tag, s.emptyTitle))
        .map((s) => `${s.rel}:${s.line} says ${s.tag} but its empty state says ${JSON.stringify(s.emptyTitle)}`)
      expect(drifted, `the loading noun drifted from the empty-state copy:\n${drifted.join('\n')}`).toEqual([])
    }
  })

  it('a "No …" in reach is a CANDIDATE, not a licence — the excluded ones stay bare', () => {
    for (const [rel, why] of EXCLUDED) {
      const site = all.find((s) => s.rel === rel && !FROM_EMPTY_STATE.some(([r]) => r === rel))
        ?? all.find((s) => s.rel === rel)
      expect(site, `${rel} left the census`).toBeTruthy()
      expect(/what=/.test(site!.tag), `${rel} must stay bare — ${why}`).toBe(false)
    }
  })

  it('the pairs really are the two branches of one gate', () => {
    const far = errPaired.filter((s) => Math.abs(s.errDist ?? 99) > 3).map((s) => `${s.rel}:${s.line} (${s.errDist})`)
    expect(far, `these matched a distant LoadError — check they describe the same data:\n${far.join('\n')}`).toEqual([])
  })

  it('a results noun only reaches a skeleton in its own component', () => {
    const leaked = all.filter((s) => s.resultsNoun && s.rel === 'features/knowledge/KnowledgeListPage.tsx'
      && s.owner !== 'KnowledgeListPage')
    expect(leaked.map((s) => `${s.line} in ${s.owner}`), 'a sub-view borrowed the list noun').toEqual([])
    const peek = all.find((s) => s.rel === 'features/knowledge/KnowledgeListPage.tsx' && s.owner === 'KnowledgeListPage'
      && !/what=/.test(s.tag))
    expect(peek, 'the peek-panel skeleton is gated on other state and stays bare').toBeTruthy()
  })

  it('the unnamed ones stay bare rather than guessing', () => {
    const invented = all.filter((s) => /what=/.test(s.tag) && !s.errNoun && !s.resultsNoun
      && !(s.emptyTitle && carriesFromEmpty(s.tag, s.emptyTitle)))
      .map((s) => `${s.rel}:${s.line} — ${s.tag}`)
    expect(invented, `these invent a noun with no declaration to source it from:\n${invented.join('\n')}`).toEqual([])
  })

  it('both canonical patterns are intact — a failure and a count each name the data', () => {
    expect(readFileSync(join(SRC, 'shared/ui/ListScaffold.tsx'), 'utf8'), 'LoadError').toMatch(/what: string/)
    const controls = readFileSync(join(SRC, 'shared/ui/ListControls.tsx'), 'utf8')
    expect(controls, 'ListControls results contract').toMatch(/results\?: \{ count: number; noun: string/)
    expect(controls, 'and it is spoken aloud').toMatch(/ResultAnnouncement/)
  })
})
