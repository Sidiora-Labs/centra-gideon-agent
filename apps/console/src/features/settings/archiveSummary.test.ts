import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import type { PortabilityManifest } from '../../shared/data/api'
import { archiveAreas, archiveInventory, archiveWhen } from './PortabilityPanel'


const V3: PortabilityManifest = {
  version: 3, format: 'zip', created_at: '2026-08-19T18:13:44Z',
  hostname: 'c0c7db0cedc6', user: 'golani',
  contents: { 'workspace/knowledge/knowledge.db': 253952, run_history_files: 0, skill_count: 25 },
  scope: 'full', domains: ['knowledge', 'memory'], verified: true,
  domain_counts: {
    knowledge: { files: 2, bytes: 299008, rows: 0 },
    memory: { files: 1, bytes: 225280, rows: 0 },
    work: { files: 8, bytes: 20685, rows: 0 },
  },
}

describe('the archive card answers in areas, not in store paths', () => {
  it('reads the per-area summary the server already computed', () => {
    const rows = archiveAreas(V3)
    expect(rows.map((r) => r.label), 'biggest first, so the eye lands on what matters')
      .toEqual(['knowledge', 'memory', 'work'].map((k) => k[0].toUpperCase() + k.slice(1)))
    expect(rows[0].detail).toBe('2 files · 292 KB')
    expect(rows[1].detail, 'singular at one').toBe('1 file · 220 KB')
  })

  it('no row can carry a raw byte count or a store path', () => {
    for (const r of archiveAreas(V3)) {
      expect(r.detail, `${r.label}: bytes must be formatted`).not.toMatch(/\b\d{5,}\b/)
      expect(r.label, 'and a label is an area, not a file').not.toMatch(/[/.]/)
    }
  })

  it('an archive with no per-area summary still answers — with its inventory', () => {
    const v1 = { ...V3, version: 1, verified: false, domain_counts: undefined } as PortabilityManifest
    expect(archiveAreas(v1), 'no areas to show').toEqual([])
    const inv = archiveInventory(v1)
    expect(inv.map((r) => r.label), 'the zero row is dropped, the rest kept')
      .toEqual(['workspace/knowledge/knowledge.db', 'skill_count'])
    expect(inv[0].detail, 'a path-keyed value is bytes').toBe('248 KB')
    expect(inv[1].detail, 'and a count stays a count').toBe('25')
  })

  it('a nested map is spelled out, never stringified', () => {
    const m = { ...V3, domain_counts: undefined,
      contents: { store_files: { agents: 3, apps: 61, sessions: 0 } } } as unknown as PortabilityManifest
    const [row] = archiveInventory(m)
    expect(row.detail).toBe('agents 3, apps 61')
    expect(row.detail, 'no braces, no quotes').not.toMatch(/[{}"]/)
  })

  it('the timestamp is the reader\'s, not the archive\'s UTC', () => {
    const out = archiveWhen('2026-08-19T18:13:44Z')
    expect(out, 'no ISO marker survives').not.toMatch(/[TZ]/)
    expect(out).toMatch(/^\d{4}-\d\d-\d\d \d\d:\d\d$/)
    expect(archiveWhen('whenever')).toBe('whenever')
  })

  it('the card renders through these, and the raw map is gone', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/PortabilityPanel.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src, 'areas first, inventory as the fallback').toMatch(/const rows = areas\.length \? areas : archiveInventory\(manifest\)/)
    expect(src, 'the header uses the localised stamp').toMatch(/archiveWhen\(manifest\.created_at\)/)
    expect(src, 'JSON.stringify must not come back').not.toMatch(/JSON\.stringify\(v\)/)
    expect(src, 'nor the raw contents map').not.toMatch(/Object\.entries\(manifest\.contents \|\| \{\}\)\.map/)
    expect(src, 'and the tenth byte formatter was not written here')
      .toMatch(/import \{ humanBytes \} from '\.\.\/\.\.\/shared\/data\/chunkedUpload'/)
  })
})
