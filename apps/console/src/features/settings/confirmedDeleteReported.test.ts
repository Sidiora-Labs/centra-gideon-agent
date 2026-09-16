import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(process.cwd(), "src/features")
const MEM = readFileSync(join(PAGES, 'settings', 'MemoryPanel.tsx'), 'utf8')

describe('a confirmed delete reports its failure', () => {
  it('all three memory deletes are wrapped, not swallowed', () => {
    for (const call of ['deleteSemantic', 'deleteEpisodic', 'deleteLesson']) {
      expect(MEM, `${call} must still perform the delete`).toMatch(new RegExp(`api\\.${call}\\(`))
      const at = MEM.indexOf(`api.${call}(`)
      const chain = MEM.slice(at - 90, at + 160)
      expect(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain), `${call}: a silent catch hides a refused delete`).toBe(false)
      expect(/try \{ await api\./.test(chain), `${call}: the rejection must be captured`).toBe(true)
    }
  })

  it('the failure is reported with this file’s own idiom and the server’s message', () => {
    expect(MEM).toMatch(/notify\(`Couldn't delete this \$\{what\}: \$\{String\(\(e as Error\)\?\.message \|\| e\)\}`, 'error'\)/)
  })

  it('each branch names WHAT failed, not a generic "item"', () => {
    for (const what of ["fail\\('memory', e\\)", "fail\\('episodic memory', e\\)", "fail\\('lesson', e\\)"])
      expect(MEM, `missing ${what}`).toMatch(new RegExp(what))
  })

  it('a failed delete KEEPS the selection so the row can be retried from', () => {
    const at = MEM.indexOf('const fail = (what: string, e: unknown)')
    expect(at, 'the fail helper exists').toBeGreaterThan(-1)
    const body = MEM.slice(at, at + 260)
    expect(body, 'the selection must survive a failure').not.toMatch(/setSelUid\(null\)/)
    expect(body, 'but the list still refetches, to show the server’s truth').toMatch(/reloadAll\(\)/)
  })

  it('the happy path still clears the selection and refetches', () => {
    expect(MEM).toMatch(/\} else return\s*\n\s*setSelUid\(null\); reloadAll\(\)/)
  })

  it('the four second-slice sites now report instead of swallowing', () => {
    const slice: Array<[string, string]> = [
      [join('knowledge', 'KnowledgeListPage.tsx'), 'deleteKnowledgeCollection'],
      [join('loops', 'DesignCockpitPage.tsx'), 'deleteULoop'],
      [join('loops', 'LoopCockpitPage.tsx'), 'deleteULoop'],
      [join('loops', 'LoopsListPage.tsx'), 'deleteULoop'],
    ]
    const swallowing: string[] = []
    for (const [rel, call] of slice) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      const at = src.indexOf(`api.${call}(`)
      expect(at, `${rel} must still perform the delete`).toBeGreaterThan(-1)
      if (/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(src.slice(at, at + 160))) swallowing.push(`${rel}:${call}`)
      if (rel === join('loops', 'LoopsListPage.tsx')) {
        expect(src, `${rel} must report through the shared reporter`)
          .toMatch(/reportingWrite\('delete this loop', \(\) => api\.deleteULoop\(id\)\)/)
      } else {
        expect(src, `${rel} must import the toast it reports through`)
          .toMatch(/import \{ notify \} from '\.\.\/\.\.\/app\/shell\/appSdk'/)
        expect(src, `${rel} must report the failure`).toMatch(/notify\(`Couldn't delete/)
      }
    }
    expect(swallowing, 'no second-slice delete may swallow its rejection').toEqual([])
  })

  it('the two cockpits no longer navigate away on a FAILED delete', () => {
    for (const rel of [join('loops', 'DesignCockpitPage.tsx'), join('loops', 'LoopCockpitPage.tsx')]) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      const at = src.indexOf('api.deleteULoop(')
      const chain = src.slice(at, at + 260)
      expect(chain, `${rel}: the failure path must not fall through to the navigation`)
        .toMatch(/catch \(e\) \{ notify\([\s\S]{0,160}?\); return \}/)
    }
  })


  const DIALOG_CONFIRM = /(?:await confirm\(|(?<![\w.$])confirmDelete\()/
  const ARMED_CONFIRM = /if \([^)\n]{0,70}confirm[A-Za-z]*[^)\n]{0,70}\)[^\n]{0,220}return/
  const SWALLOWED = /\.catch\(\s*\(\s*\)\s*⇒\s*\{\s*\}\s*\)/

  const allPages = (): string[] => {
    const walk = (dir: string, out: string[] = []): string[] => {
      for (const name of readdirSync(dir)) {
        const abs = join(dir, name)
        if (statSync(abs).isDirectory()) walk(abs, out)
        else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
      }
      return out
    }
    return walk(PAGES)
  }

  type Gated = { rel: string; line: number; call: string; idiom: 'dialog' | 'arm'; swallowed: boolean }

  const confirmGatedCalls = (): Gated[] => {
    const out: Gated[] = []
    for (const abs of allPages()) {
      const src = readFileSync(abs, 'utf8').replace(/=>/g, '⇒')
      for (const m of src.matchAll(/api\.(\w+)\(/g)) {
        const before = src.slice(Math.max(0, m.index! - 900), m.index!)
        const dialog = DIALOG_CONFIRM.test(before)
        if (!dialog && !ARMED_CONFIRM.test(before)) continue
        out.push({
          rel: abs.replace(PAGES + '/', ''),
          line: src.slice(0, m.index).split('\n').length,
          call: m[1],
          idiom: dialog ? 'dialog' : 'arm',
          swallowed: SWALLOWED.test(src.slice(m.index!, m.index! + 200)),
        })
      }
    }
    return out
  }

  it('the sweep is not vacuous — the pages, BOTH idioms, and a spread of areas', () => {
    expect(allPages().length, 'the page tree must be discoverable').toBeGreaterThan(150)
    const gated = confirmGatedCalls()
    expect(gated.filter((g) => g.idiom === 'dialog').length, 'the dialog predicate must match').toBeGreaterThan(20)
    expect(gated.filter((g) => g.idiom === 'arm').length, 'the two-step-arm predicate must match too').toBeGreaterThan(3)
    expect(new Set(gated.map((g) => g.rel.split('/')[0])).size, 'and reach across areas').toBeGreaterThan(3)
  })

  it('NO confirmed action anywhere swallows its rejection — by shape, both idioms', () => {
    const offenders = confirmGatedCalls()
      .filter((g) => g.swallowed)
      .map((g) => `${g.rel}:${g.line} (api.${g.call}, ${g.idiom})`)
    expect(offenders, 'the user was stopped and asked to confirm — silence is indefensible').toEqual([])
  })

  it('the confirmed audit-log rotation the verb filter could not see now reports', () => {
    const src = readFileSync(join(PAGES, 'settings', 'AuditPanel.tsx'), 'utf8')
    expect(src, 'the rotation must still be confirmed first').toMatch(/await confirm\(\{/)
    expect(src, 'and the rejection captured, not discarded').toMatch(/try \{\s+const res = await api\.selRotate\(\)/)
    expect(src, 'reported with the server’s own message').toMatch(/notify\(`Couldn't archive the audit log: \$\{msg\}`, 'error'\)/)
    const at = src.indexOf('api.selRotate(')
    expect(src.slice(at, at + 640), 'a failed rotation must not invalidate or reload').toMatch(/return {3}\/\/ nothing archived/)
    expect(confirmGatedCalls().some((g) => g.rel === join('settings', 'AuditPanel.tsx') && g.call === 'selRotate'))
      .toBe(true)
  })

  it('the two-step arm sites are inside the sweep — including the two no list covered', () => {
    const arm = confirmGatedCalls().filter((g) => g.idiom === 'arm')
    for (const [rel, call] of [
      [join('chat', 'SdlcProgressCard.tsx'), 'deleteULoop'],
      [join('knowledge', 'KnowledgeListPage.tsx'), 'deleteKnowledgeIntent'],
      [join('loops', 'LoopsListPage.tsx'), 'deleteULoop'],
      [join('loops', 'DesignCockpitPage.tsx'), 'deleteULoop'],
      [join('loops', 'LoopCockpitPage.tsx'), 'deleteULoop'],
    ]) {
      expect(arm.some((g) => g.rel === rel && g.call === call), `${rel}:${call} must be in scope`).toBe(true)
    }
  })

  it('the gate does not fire on a read that merely sits near a confirm STATE variable', () => {
    const flagged = confirmGatedCalls().filter(
      (g) => g.rel === join('loops', 'DesignCockpitPage.tsx') && ['project', 'uLoopDesignTokens'].includes(g.call),
    )
    expect(flagged, 'these are reads in loadLoop/loadTokens, not confirmed deletes').toEqual([])
  })

})
