import { describe, it, expect } from 'vitest'
import { readdirSync } from 'node:fs'
import { join } from 'node:path'
import { extractUiProps } from '../../../tooling/extractUiProps.mjs'
import type { UiDoc } from './uiDoc'


const UI_DIR = join(process.cwd(), "src/shared/ui")

const docModules = import.meta.glob<{ default: UiDoc | UiDoc[] }>('./*.doc.ts', {
  eager: true,
})

const authored = new Map<string, UiDoc>()
for (const [path, mod] of Object.entries(docModules)) {
  const exported = mod.default
  const list = Array.isArray(exported) ? exported : [exported]
  for (const doc of list) {
    expect(doc?.name, `doc file ${path} default-exports a UiDoc with a name`).toBeTruthy()
    expect(authored.has(doc.name), `duplicate doc for ${doc.name} (in ${path})`).toBe(false)
    authored.set(doc.name, doc)
  }
}

const { components: derived } = extractUiProps(UI_DIR)
const derivedNames = Object.keys(derived).sort()

describe('ui-docs: documentation-as-data drift guard', () => {
  it('discovers the ui/ primitive kit', () => {
    expect(derivedNames.length).toBeGreaterThan(40)
  })

  it('every exported ui/ component has a doc object', () => {
    const undocumented = derivedNames.filter((n) => !authored.has(n))
    expect(
      undocumented,
      `These ui/ components have no <Name>.doc.ts entry — add one:\n${undocumented.join('\n')}`
    ).toEqual([])
  })

  it('every doc object maps to a real exported component', () => {
    const orphans = [...authored.keys()].filter((n) => !(n in derived)).sort()
    expect(
      orphans,
      `These doc objects name no exported ui/ component (renamed/removed?):\n${orphans.join('\n')}`
    ).toEqual([])
  })

  it('each doc names exactly the props the component declares', () => {
    const mismatches: Record<string, { missing: string[]; extra: string[] }> = {}
    for (const name of derivedNames) {
      const doc = authored.get(name)
      if (!doc) continue
      const declared = new Set(derived[name].map((p) => p.name))
      const documented = new Set((doc.props || []).map((p) => p.name))
      const missing = [...declared].filter((p) => !documented.has(p)).sort()
      const extra = [...documented].filter((p) => !declared.has(p)).sort()
      if (missing.length || extra.length) mismatches[name] = { missing, extra }
    }
    expect(
      mismatches,
      `Prop drift (missing = declared-but-undocumented; extra = documented-but-gone):\n${JSON.stringify(mismatches, null, 2)}`
    ).toEqual({})
  })

  it('each doc carries the required semantic fields', () => {
    const thin: Record<string, string[]> = {}
    for (const [name, doc] of authored) {
      const problems: string[] = []
      if (!doc.keywords?.length) problems.push('no keywords')
      if (!doc.description?.trim()) problems.push('no description')
      if (!doc.bestPractices?.length) problems.push('no bestPractices')
      for (const p of doc.props || []) {
        if (!p.description?.trim()) problems.push(`prop ${p.name} has no description`)
      }
      if (problems.length) thin[name] = problems
    }
    expect(
      thin,
      `These docs are missing required semantic fields:\n${JSON.stringify(thin, null, 2)}`
    ).toEqual({})
  })
})

describe('ui-docs: doc-file convention', () => {
  it('finds a .doc.ts for every source file that declares components', () => {
    const sourceFiles = readdirSync(UI_DIR).filter(
      (f) => /\.tsx?$/.test(f) && !/\.(test|doc)\.tsx?$/.test(f)
    )
    expect(sourceFiles.length).toBeGreaterThan(40)
  })
})
