import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { confirmCopy } from './DurabilityPanel'
import type {
  DurabilityHistoryEntry, DurabilityHistoryPreview, DurabilityHistoryDiffFile,
} from '../../shared/data/api'


const entry: DurabilityHistoryEntry = {
  sha: 'a'.repeat(40), short: 'aaaaaaa', at: 1_700_000_000, subject: 'tidy the ledger',
  surface: 'workflows', unattended: false,
}
const file = (path: string): DurabilityHistoryDiffFile =>
  ({ path, status: 'M', bytes: 12, rendered: true, diff: '' })
const preview = (files: string[], rolledAway: number): DurabilityHistoryPreview => ({
  operation: 'rollback', root: '/w', target: 'b'.repeat(40), head: 'c'.repeat(40),
  files: files.map(file), commits_rolled_away: rolledAway, reversible: true, paths: [],
})
const pending = (paths: string[], universe: string[], rolledAway: number) => ({
  entry, op: 'rollback' as const, head: 'c'.repeat(40),
  preview: preview(universe, rolledAway), paths, files: universe.map(file),
})

describe('the durability dialogs agree with their own counts', () => {
  it('never ships a parenthetical plural (the shortcut this replaced)', () => {
    const shapes = [
      confirmCopy('rollback', '/w', entry, pending([], ['a.ts'], 1)),
      confirmCopy('rollback', '/w', entry, pending(['a.ts'], ['a.ts'], 1)),
      confirmCopy('rollback', '/w', entry, pending(['a.ts'], ['a.ts', 'b.ts'], 4)),
      confirmCopy('revert', '/w', entry, pending([], ['a.ts'], 1)),
      confirmCopy('revert', '/w', entry, pending(['a.ts'], ['a.ts', 'b.ts'], 4)),
    ]
    for (const s of shapes) {
      expect(s.title, `"${s.title}" still hedges`).not.toMatch(/\((s|es)\)/)
      expect(s.body, `"${s.body.slice(0, 60)}…" still hedges`).not.toMatch(/\((s|es)\)/)
    }
    expect(shapes.every((s) => s.title.length > 20 && s.body.length > 80)).toBe(true)
  })

  it('ONE picked file reads singular in the title and in the body', () => {
    const one = confirmCopy('rollback', '/w', entry, pending(['a.ts'], ['a.ts'], 1))
    expect(one.title).toBe('Roll back 1 of 1 file to this point?')
    expect(one.body, 'the selection noun, not the denominator noun').toContain('Only the 1 file you picked')
    expect(one.body, 'and the commit count agrees too').toContain('The 1 change made since')
    expect(one.body).toContain('set aside for those 1 file')
  })

  it('the DENOMINATOR governs the "n of m" noun, not the selection', () => {
    const mixed = confirmCopy('rollback', '/w', entry, pending(['a.ts'], ['a.ts', 'b.ts'], 4))
    expect(mixed.title).toBe('Roll back 1 of 2 files to this point?')
    expect(mixed.body, 'while the SELECTION stays singular in the same sentence').toContain(
      'Only the 1 file you picked',
    )
    expect(mixed.body).toContain('The 4 changes made since')
  })

  it('the whole-root body pluralises on the commit count alone', () => {
    expect(confirmCopy('rollback', '/w', entry, pending([], ['a.ts', 'b.ts'], 1)).body)
      .toContain('The 1 change made since')
    expect(confirmCopy('rollback', '/w', entry, pending([], ['a.ts', 'b.ts'], 9)).body)
      .toContain('The 9 changes made since')
  })

  it('the undo dialog agrees on both numbers too', () => {
    expect(confirmCopy('revert', '/w', entry, pending(['a.ts'], ['a.ts'], 1)).title)
      .toBe('Undo just this change in 1 of 1 file?')
    expect(confirmCopy('revert', '/w', entry, pending(['a.ts'], ['a.ts', 'b.ts'], 1)).title)
      .toBe('Undo just this change in 1 of 2 files?')
    expect(confirmCopy('revert', '/w', entry, pending(['a.ts'], ['a.ts'], 1)).body)
      .toContain('to the 1 file you picked')
  })

  it('and the held-preview summary — the one line NOT reachable through confirmCopy', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/DurabilityPanel.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const at = src.indexOf('function PreviewCard(')
    expect(at, 'PreviewCard must still exist').toBeGreaterThan(-1)
    const next = src.indexOf('\nfunction ', at + 1)
    expect(next, 'a top-level function must follow PreviewCard').toBeGreaterThan(at)
    const body = src.slice(at, next)
    expect(body, 'the summary must not hedge either').not.toMatch(/\((s|es)\)/)
    expect(body, 'and it must derive its three nouns from its three counts').toMatch(
      /const previewWord = preview\.files\.length === 1/,
    )
    expect(body).toMatch(/const fileWord = files\.length === 1/)
    expect(body).toMatch(/const changeWord = preview\.commits_rolled_away === 1/)
  })
})
