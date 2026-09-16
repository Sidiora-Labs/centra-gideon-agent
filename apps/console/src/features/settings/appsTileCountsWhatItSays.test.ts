import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const src = readFileSync(join(process.cwd(), "src/features/settings/settingsWidgets.tsx"), 'utf8')

const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '').replace(/\{\/\*[\s\S]*?\*\/\}/g, '')

function appsTileBlock(): string {
  const start = src.indexOf("id: 'apps',")
  expect(start).toBeGreaterThan(-1)
  const rest = src.slice(start)
  const next = rest.slice(1).search(/^\s*id: '/m)
  return stripComments(next > 0 ? rest.slice(0, next + 1) : rest)
}

describe('the Apps settings tile counts what it says (#615)', () => {
  it('captions the stat with the subset it actually counts', () => {
    const block = appsTileBlock()
    expect(block).toContain("'app with settings'")
    expect(block).toContain("'apps with settings'")
    expect(block).toMatch(/BigStat\s+value=\{configurable\}/)
  })

  it('no longer captions a filtered count with the unqualified noun', () => {
    const block = appsTileBlock()
    expect(block).not.toMatch(/BigStat\s+value=\{nonProvider\.length\}/)
    expect(block).not.toContain("'installed apps'")
    expect(block).not.toContain("'installed app'")
  })

  it('keeps the installed total as context so 0 cannot read as "no apps"', () => {
    const block = appsTileBlock()
    expect(block).toContain('of ${data.length} installed')
    expect(block).toContain('Nothing installed yet')
  })
})
