import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'


const SRC = resolve(__dirname, "AppsSection.tsx")
const API = resolve(__dirname, "../../shared/data/api.ts")

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/(^|[^:])\/\/.*$/gm, '$1')
}

const code = stripComments(readFileSync(SRC, 'utf8'))
const api = stripComments(readFileSync(API, 'utf8'))

describe('the middle removal rung exists as a real control (issue 2541)', () => {
  it('the force-uninstall dialog no longer points at a control that does not exist', () => {
    expect(code).not.toContain('To just turn the app off (keeping its files), use Uninstall instead')
    expect(code).not.toMatch(/turn the app off[^.]*use <?span?[^>]*>?Uninstall/)
  })

  it('names BOTH lesser rungs, each by what it actually does', () => {
    expect(code).toMatch(/To keep your data, use[\s\S]{0,60}Uninstall/)
    expect(code).toMatch(/turn the app off and leave everything on disk, use[\s\S]{0,60}Deactivate/)
  })

  it('renders an Uninstall control in the detail panel, wired to the middle rung', () => {
    expect(code).toMatch(/setConfirmRemove\(true\)[\s\S]{0,120}Uninstall<\/Button>/)
    expect(code).toContain('RemoveAppModal')
    expect(code).toMatch(/api\.removeApp\(name\)/)
  })

  it('offers the safe rung wherever it offers the destructive one', () => {
    const uninstallRows = code.match(/label\s*[=:]\s*['"]Uninstall…['"]/g) ?? []
    const forceRows = code.match(/label\s*[=:]\s*['"]Force uninstall…['"]/g) ?? []
    expect(forceRows.length).toBeGreaterThanOrEqual(2)
    expect(uninstallRows.length).toBe(forceRows.length)
  })

  it('force uninstall still calls the destructive endpoint — the rung did not move', () => {
    expect(code).toMatch(/api\.uninstallApp\(name,\s*true\)/)
    expect(api).toContain("?force=1")
    expect(api).toContain("?remove=1")
  })

  it('keeps the three client calls distinct so a call site cannot land on the wrong rung', () => {
    expect(api).toMatch(/uninstallApp:\s*\(name: string, force = false\)/)
    expect(api).toMatch(/removeApp:\s*\(name: string\)/)
  })

  it('states the data outcome from the two SEPARATE facts, not one truthiness', () => {
    expect(code).toMatch(/!facts\.present/)
    expect(code).toMatch(/facts\.entries === 0/)
    expect(api).toContain('AppDataFacts')
  })

  it('says an earlier unconsumed copy will BLOCK the removal, and names the paths (2585)', () => {
    expect(api).toContain('unconsumed?: string[]')
    expect(code).toMatch(/facts\?\.unconsumed \?\? \[\]/)
    expect(code).toMatch(/const blocked = unconsumed\.length > 0/)
    expect(code).toMatch(/disabled=\{blocked\}/)
    expect(code).toMatch(/disabledReason=\{blocked \?/)
    expect(code).toMatch(/unconsumed\.map\(\(p\) => \([\s\S]{0,200}\{p\}/)
    expect(code).toMatch(/role="alert"/)
  })
})
