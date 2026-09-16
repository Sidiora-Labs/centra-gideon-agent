import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/tools/ToolsPage.tsx"), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const TOGGLE_WRITES = ['toggleMcpServer', 'toggleMcpTool', 'toggleTool', 'toggleToolProvider']

describe('a tool toggle that fails tells the user', () => {
  it('the reporter is the SHARED one, and this file keeps no copy of it', () => {
    expect(CODE, 'the shared contract is imported').toMatch(
      /import \{ reportingWrite \} from '\.\.\/\.\.\/app\/shell\/reportingWrite'/,
    )
    const localDefs = [...CODE.matchAll(/(function|const)\s+reportingWrite\b\s*[=(]/g)]
    expect(localDefs.length, 'a page-local copy would shadow the shared one silently').toBe(0)

    const shared = readFileSync(join(process.cwd(), "src/app/shell/reportingWrite.ts"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^\s*\/\/.*$/gm, '')
    expect(shared).toMatch(
      /export async function reportingWrite\(what: string, run: \(\) => Promise<unknown>\): Promise<boolean>/,
    )
    expect(shared, 'it reports through the app toast').toMatch(/notify\(failureSentence\(what, e\), 'error'\)/)
    expect(shared, 'the sentence still opens with the written clause').toMatch(/`Couldn't \$\{what\}: \$\{detail\}`/)
    expect(shared, 'and closes cleanly when there is no usable detail').toMatch(/`Couldn't \$\{what\}\.`/)
    expect(shared, 'and returns the outcome so a caller can skip its refetch').toMatch(/return true/)
    expect(shared, 'no dead JSON unwrap').not.toMatch(/JSON\.parse\(msg\)/)
  })

  it('no toggle write swallows its rejection', () => {
    const offenders: string[] = []
    for (const call of TOGGLE_WRITES) {
      for (const m of CODE.matchAll(new RegExp(`api\\.${call}\\(`, 'g'))) {
        const chain = CODE.slice(m.index!, m.index! + 200)
        if (/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain)) offenders.push(call)
      }
    }
    expect(offenders, 'a silently dead toggle is the bug this file already named').toEqual([])
  })

  const ALSO_ROUTED = ['probeMcp']

  it('all four toggles go through the one reporter, and nothing unexpected does', () => {
    let toggles = 0
    for (const m of CODE.matchAll(/reportingWrite\([\s\S]{0,140}?api\.(\w+)\(/g)) {
      if ((TOGGLE_WRITES as readonly string[]).includes(m[1])) { toggles++; continue }
      expect(ALSO_ROUTED, `unexpected call routed: ${m[1]}`).toContain(m[1])
    }
    expect(toggles, 'every toggle write routed through reportingWrite').toBe(4)
  })

  it('the allowlist is not a dumping ground', () => {
    for (const call of ALSO_ROUTED) {
      expect(CODE, `${call} no longer exists — drop it from the allowlist`).toContain(`api.${call}(`)
    }
    expect(ALSO_ROUTED.length, 'a growing list means the rule needs rethinking').toBeLessThan(4)
  })

  it('a failed write SKIPS the refetch — the point of returning the outcome', () => {
    const gated = [...CODE.matchAll(/if \(ok\) setTimeout\(load, \d+\)/g)]
    expect(gated.length, 'callers gating the refetch on success').toBe(3)
    expect(CODE).not.toMatch(/await reportingWrite\([\s\S]{0,160}?\)\s*\n\s*setTimeout\(load/)
  })

  it('each report names WHICH toggle failed, with its subject', () => {
    expect(CODE).toMatch(/\$\{s\.enabled \? 'disable' : 'enable'\} "\$\{s\.name\}"/)
    expect(CODE).toMatch(/\$\{enabled \? 'enable' : 'disable'\} "\$\{t\.name\}"/)
    expect(CODE).toMatch(/\$\{g\.providerDisabled \? 'enable' : 'disable'\} "\$\{g\.key\}"/)
  })

  it('the switches are still DATA-DRIVEN — the premise of the whole finding', () => {
    expect(SRC).toMatch(/<Toggle on=\{!!g\.server\.enabled\} \/>/)
  })

  it('reconnectServer keeps its documented swallow — the deliberate non-fix', () => {
    expect(SRC).toMatch(/await api\.reconnectMcp\(s\.name\) \} catch \{ \/\* status surfaces on reload \*\/ \}/)
  })

  it('removeServer’s original reporting is untouched — this converged ONTO it', () => {
    expect(CODE).toMatch(/await api\.removeMcpServer\(s\.name\)/)
    expect(CODE).toMatch(/notify\(msg, 'error'\)/)
  })
})
