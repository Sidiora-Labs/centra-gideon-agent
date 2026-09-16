import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { CACHE_NAMESPACES, namespaceOf } from './keys'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) ? [p] : []
  })

const isTest = (p: string) => /\.(test|doc)\./.test(p)
const rel = (p: string) => p.slice(SRC.length + 1)
const codeOf = (p: string) =>
  readFileSync(p, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')

const PRODUCTION = walk(SRC).filter((p) => !isTest(p))
const EVERY = walk(SRC)

describe('§1 the deleted helper stays deleted', () => {
  const GONE = [
    /\buseCachedData\s*[<(]/,
    /\binvalidateCache\s*\(/,
    /\bpeekCache\s*\(/,
    /\bwriteCache\s*\(/,
    /from '[^']*lib\/useCachedData'/,
  ]

  it('has no call site anywhere in the tree — tests and docs included', () => {
    const hits: string[] = []
    for (const p of EVERY) {
      const code = codeOf(p)
      for (const re of GONE) if (re.test(code)) hits.push(`${rel(p)} :: ${re.source}`)
    }
    expect(hits, 'a call site of the deleted helper reappeared').toEqual([])
  })

  it('and the module file itself is gone', () => {
    expect(() => readFileSync(join(SRC, "shared/data", 'useCachedData.ts'), 'utf8'))
      .toThrow()
  })

  it('VACUITY: the same scan finds the REPLACEMENT everywhere, so it is not matching nothing', () => {
    const adopters = EVERY.filter((p) => /\buseQuery\b/.test(codeOf(p)) && !p.includes('app/shell/useQueryState'))
    expect(adopters.length, 'the replacement must be everywhere the old helper was').toBeGreaterThanOrEqual(95)
    const prod = PRODUCTION.filter((p) => /\buseQuery\s*[<(]/.test(codeOf(p)) && !p.includes('app/shell/useQueryState'))
    expect(prod.length, 'and it must be the production surfaces, not only their tests').toBeGreaterThanOrEqual(68)
  })
})

describe('§2 every cache key namespace is declared', () => {
  const keyLiterals = (): { key: string; at: string }[] => {
    const out: { key: string; at: string }[] = []
    const indirect = new Set<string>()
    for (const p of PRODUCTION) {
      const code = codeOf(p)
      for (const m of code.matchAll(/(?:useQuery(?:<[^>]*>)?|writeQuery|peekQuery|peekEntry|invalidateKeys)\(\s*(['"`])([A-Za-z][\w:-]*)/g)) {
        out.push({ key: m[2], at: rel(p) })
      }
      for (const m of code.matchAll(/(?:useQuery(?:<[^>]*>)?|writeQuery|peekQuery|peekEntry|invalidateKeys)\(\s*([A-Z][A-Z0-9_]*)\b/g)) {
        indirect.add(m[1])
      }
    }
    for (const p of PRODUCTION) {
      const code = codeOf(p)
      for (const name of indirect) {
        const m = code.match(new RegExp(`const ${name} = '([A-Za-z][\\w:-]*)'`))
        if (m) out.push({ key: m[1], at: rel(p) })
      }
    }
    return out
  }

  it('resolves the keys passed by NAME too — VACUITY on the indirection', () => {
    expect(keyLiterals().map((k) => k.key)).toContain('learning:week')
  })

  it('finds a substantial population — VACUITY', () => {
    const found = keyLiterals()
    expect(found.length, 'the key census must actually find keys').toBeGreaterThan(120)
    expect(new Set(found.map((k) => namespaceOf(k.key))).size).toBeGreaterThan(25)
  })

  it('every namespace in use has a declared freshness policy', () => {
    const undeclared = keyLiterals()
      .filter((k) => !(namespaceOf(k.key) in CACHE_NAMESPACES))
      .map((k) => `${k.key}  (${k.at})`)
    expect([...new Set(undeclared)], 'declare these in lib/data/keys.ts').toEqual([])
  })

  it('and nothing is declared that no key uses', () => {
    const used = new Set(keyLiterals().map((k) => namespaceOf(k.key)))
    const dead = Object.keys(CACHE_NAMESPACES).filter((n) => !used.has(n))
    expect(dead, 'remove these from CACHE_NAMESPACES, or use them').toEqual([])
  })
})

describe('§3 the remaining hand-rolled server-data caches are a shrinking, named list', () => {
  const REMAINING: { file: string; why: string }[] = [
    {
      file: 'features/files/filesData.ts',
      why: 'useDirCache: a per-PATH listing cache with hierarchical subtree invalidation, a 400-path'
        + ' bound, and per-path generation guards so a load that started before an invalidate cannot'
        + ' write back. The shared layer has none of those three, and inventing them inside it for one'
        + ' consumer would be the wrong trade. It is named here rather than quietly excluded: it still'
        + ' paints a stale listing with no label, which is the same defect class, one surface wide.',
    },
  ]

  const PREFERENCE_STORES: Record<string, string> = {
    'app/shell/App.tsx': 'nav rail collapsed',
    'app/shell/appearance.tsx': 'per-scheme appearance overrides',
    'features/tasks/TasksListPage.tsx': 'view / sort / scope choice',
    'features/terminal/TerminalPage.tsx': 'user-renamed terminal tab labels',
    'features/files/FilesSection.tsx': 'which files tab was last open',
    'features/files/browse/useFileTabs.ts': 'the set of open file tabs and the active one',
    'features/loops/DesignCockpitPage.tsx': 'canvas card order within one loop',
    'features/dashboard/widgets/DesktopLiveView.tsx': 'the mirror/overlay view toggles',
    'app/shell/pushClient.ts': "this browser profile's own push device id",
    'shared/ui/Composer.tsx': 'the composer resting height the reader dragged to',
  }

  const handRolled = () => PRODUCTION.filter((p) => {
    const code = codeOf(p)
    if (!/(session|local)Storage\.setItem/.test(code)) return false
    if (!/(session|local)Storage\.getItem/.test(code)) return false
    return /\bapi\.\w+\(/.test(code) || /\bFsEntry\b|\bChatDetail\b/.test(code)
  }).map(rel).filter((r) => !(r in PREFERENCE_STORES))

  it('VACUITY: the detector still recognises the shape it is counting', () => {
    expect(handRolled(), 'the detector found no hand-rolled cache at all').toContain('features/files/filesData.ts')
  })

  it('the list is EXACTLY the named remainder — a new one fails, and removing one fails too', () => {
    expect(handRolled().sort()).toEqual(REMAINING.map((r) => r.file).sort())
  })

  it('every named preference store still exists — the allowlist cannot rot silently', () => {
    const all = PRODUCTION.map(rel)
    for (const f of Object.keys(PREFERENCE_STORES)) {
      expect(all, `${f} is allowlisted as a preference store but no longer exists`).toContain(f)
    }
  })

  it('each remaining entry states WHY, at length', () => {
    for (const r of REMAINING) {
      expect(r.why.length, `${r.file} needs a real reason, not a shrug`).toBeGreaterThan(120)
    }
  })

  it('the two that were converged are gone for good', () => {
    const chat = codeOf(join(SRC, "features", 'ChatPage.tsx'))
    expect(chat, 'the private chat-detail prefix must not come back').not.toMatch(/'chat-detail:'/)
    expect(chat, 'and the transcript seed goes through the shared store').toMatch(/peekQuery</)
  })
})

describe('§4 the stale-paint label is adopted and its adoption only grows', () => {
  const adopters = () => PRODUCTION.filter((p) => /<StaleNotice\b/.test(codeOf(p))).map(rel)
  const passesStale = () => PRODUCTION.filter((p) => /\bstale=\{/.test(codeOf(p))).map(rel)

  it('the shared primitives carry it, so their consumers inherit it', () => {
    expect(adopters()).toEqual(expect.arrayContaining([
      'shared/ui/ListControls.tsx',
      'features/settings/bento.tsx',
    ]))
  })

  it('the surfaces that PASS a stale flag are a growing set, floor pinned', () => {
    const n = passesStale().length
    expect(n, 'a surface stopped labelling its stale paint').toBeGreaterThanOrEqual(4)
  })

  it('and the named list surfaces label theirs BY NAME', () => {
    const named = [
      'features/inbox/InboxPage.tsx',
      'features/knowledge/KnowledgeListPage.tsx',
      'features/workflows/WorkflowsListPage.tsx',
      'features/settings/settingsWidgets.tsx',
    ]
    expect(passesStale()).toEqual(expect.arrayContaining(named))
  })

  it('nothing keys a freshness label on `loading` or `revalidating`', () => {
    const wrong: string[] = []
    for (const p of PRODUCTION) {
      const code = codeOf(p)
      for (const m of code.matchAll(/stale=\{([^}]*)\}/g)) {
        const expr = m[1]
        if (/\bloading\b|\brevalidating\b/.test(expr) && !/stale/i.test(expr)) {
          wrong.push(`${rel(p)}: stale={${expr}}`)
        }
      }
    }
    expect(wrong, 'pass `stale`, not `loading`/`revalidating`').toEqual([])
  })
})
