import ts from 'typescript'
import { createElement } from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { vi } from 'vitest'
import { Button } from './Button'
import { jsxTags } from '../testing/jsxContracts'
import { sourceFile } from '../testing/sourceOwners'
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

const FIXED: Array<{ file: string; reason: string; guard: RegExp }> = [
  { file: 'features/settings/AccountPanel.tsx', reason: 'No changes to save', guard: /aria-disabled=\{!dirty \|\| undefined\}/ },
  { file: 'features/settings/AccountPanel.tsx', reason: 'No changes to save', guard: /aria-disabled=\{!botDirty \|\| undefined\}/ },
  { file: 'shared/ui/content/ContentSurface.tsx', reason: 'no changes to save', guard: /aria-disabled=\{\(!dirty && !saving\) \|\| state\.baseMissing \|\| undefined\}/ },
  { file: 'features/tasks/formControls.tsx', reason: 'That would create a dependency cycle', guard: /aria-disabled=\{cyclic \|\| undefined\}/ },
  { file: 'features/knowledge/KnowledgeDetail.tsx', reason: 'Nothing more to show', guard: /aria-disabled=\{!hasMore \|\| undefined\}/ },
]

describe('a converted raw control keeps its tab stop AND its dimming', () => {
  for (const { file, reason, guard } of FIXED) {
    it(`${file} — "${reason}"`, () => {
      const src = readFileSync(join(SRC, file), 'utf8')
      if (file === 'features/settings/AccountPanel.tsx') {
        const condition = guard.source.includes('botDirty') ? 'botDirty' : 'dirty'
        const matches = jsxTags(src, nativeControlBindings(src, ['Button'])).filter((tag) => tag.attributes.get('disabled') === `{!${condition}}`)
        expect(matches.length).toBe(1)
        const tag = matches[0]
        expect(tag.attributes.get('disabledReason')).toBe('"No changes to save"')
        expect(tag.attributes.get('onClick')).toContain(`${condition} ?`)
        expect(tag.attributes.get('className')).toContain('aria-disabled:opacity-40')
        const click = vi.fn()
        render(createElement(Button, { disabled: true, disabledReason: reason, onClick: click, className: 'aria-disabled:opacity-40', children: 'Save settings' }))
        const button = screen.getByRole('button', { name: 'Save settings' })
        expect(button).not.toBeDisabled()
        expect(button).toHaveAttribute('aria-disabled', 'true')
        expect(button).toHaveClass('aria-disabled:opacity-40')
        button.focus()
        expect(button).toHaveFocus()
        fireEvent.click(button)
        expect(click).not.toHaveBeenCalled()
        expect(button).toHaveAccessibleDescription(reason)
      } else expect(src, 'the raw gate must publish aria-disabled').toMatch(guard)
      expect(src.toLowerCase(), 'and it must say why').toContain(reason.toLowerCase())
      const softTags = [...src.matchAll(/<button\b[\s\S]{0,900}?>/g)]
        .map((m) => m[0])
        .filter((t) => /aria-disabled=/.test(t) && /disabled:opacity-40/.test(t))
      for (const t of softTags) {
        expect(t, 'a soft-off tag needs aria-disabled:opacity-40 as well').toMatch(/aria-disabled:opacity-40/)
      }
    })
  }

  it('keeps the active hover tint and neutralises it for soft-off controls', () => {
    const src = readFileSync(join(SRC, 'features/tasks/formControls.tsx'), 'utf8')
    expect(src).toMatch(/hover:bg-surface-high/)
    expect(src).toMatch(/aria-disabled:hover:bg-transparent/)
  })

  it('keeps a missing-source save focusable and explains the recovery', () => {
    const src = readFileSync(join(SRC, 'shared/ui/content/ContentSurface.tsx'), 'utf8')
    expect(src).toMatch(/onClick=\{dirty && !state\.baseMissing \? state\.save : undefined\} disabled=\{saving\}/)
    expect(src).toContain('Refresh and rebase the draft before saving; the current source is unavailable')
  })

  it('refuses the click it can no longer refuse natively', () => {
    for (const rel of ['features/settings/AccountPanel.tsx', 'features/tasks/formControls.tsx', 'features/knowledge/KnowledgeDetail.tsx', 'shared/ui/content/ContentSurface.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      if (rel === 'features/tasks/formControls.tsx') {
        expect(src, 'dependency insertion remains inside the noncyclic branch')
          .toMatch(/onClick=\{\(\) => \{ if \(!cyclic\) \{ onChange\(\[\.\.\.value, task\.id\]\); setQuery\(''\) \} \}\}/)
      } else {
        expect(src, `${rel} must guard its handler`).toMatch(/onClick=\{[^}\n]*\?[^\n]*undefined/)
      }
    }
  })
})

describe('the remaining raw disabled buttons are accounted for', () => {
  const BUSY = /\b(busy|saving|sending|loading|installing|pending|working|submitting|launching|testing|running|deleting|creating|refreshing|syncing|starting|stopping|genning|importing|exporting|uploading|repairing|regen\w*|retrying|reloading|applying|generating|fetching|polling|checking)\b/i

  const ACCOUNTED = new Map<string, string>([
    ['shared/ui/HeaderActions.tsx', 'pass-through `disabled` on a primitive — the caller owns the reason'],
    ['shared/ui/ProjectPicker.tsx', 'pass-through `disabled` on a primitive'],
    ['shared/ui/Segmented.tsx', 'pass-through `disabled` on a primitive'],
    ['shared/ui/TextLink.tsx', 'pass-through `disabled` on a primitive'],
    ['shared/ui/Toggle.tsx', 'pass-through `disabled` on a primitive'],
    ['features/settings/ProjectionRulesPanel.tsx', 'pass-through `disabled` from its caller'],
    ['features/settings/SecurityPanel.tsx', 'pass-through `disabled` from its caller'],
    ['features/loops/LoopPlanReview.tsx', 'self-evident — the label reads "Installed" when done'],
    ['features/tasks/TaskDetail.tsx', 'NOT AN ACTION: read-only + capability gates, awaiting the #1168 shape'],
  ])

  const unaccounted = walk(SRC).flatMap((f) => {
    const rel = f.slice(SRC.length + 1)
    if (ACCOUNTED.has(rel)) return []
    const src = readFileSync(f, 'utf8')
    return [...src.matchAll(/<button\b[^>]{0,600}?(?<!aria-)disabled=\{([^}]*(?:\{[^}]*\}[^}]*)*)\}/gs)]
      .filter((m) => m[1].split(/\|\||&&/).map((s) => s.trim()).filter(Boolean).some((c) => !BUSY.test(c)))
      .map(() => rel)
  })

  it('leaves none unclassified', () => {
    expect([...new Set(unaccounted)], 'a raw disabled button with a gate a user could act on').toEqual([])
  })

  it('still finds the population it is filtering (not vacuously green)', () => {
    const population = walk(SRC).flatMap((file) => {
      const source = readFileSync(file, 'utf8')
      const native = nativeControlBindings(source, ['Button', 'IconButton', 'SquareIconButton', 'QuietButton'])
      return jsxTags(source, ['button', 'motion.button', ...native]).filter((tag) => tag.attributes.has('disabled'))
    })
    expect(population.filter((tag) => ['button', 'motion.button'].includes(tag.name)).length, 'AST still resolves the original raw population without a bounded regex').toBeGreaterThanOrEqual(30)
    expect(population.filter((tag) => !['button', 'motion.button'].includes(tag.name)).length, 'native migrations remain independently represented').toBeGreaterThanOrEqual(30)
  })
})

function nativeControlBindings(source: string, symbols: readonly string[]): string[] {
  return sourceFile(source).statements.flatMap((statement) => {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) return []
    const module = statement.moduleSpecifier.text
    const named = statement.importClause?.namedBindings
    if (!named || !ts.isNamedImports(named)) return []
    return named.elements.filter((binding) => {
      const symbol = (binding.propertyName ?? binding.name).text
      return symbols.includes(symbol) && module.endsWith(`/${symbol}`)
    }).map((binding) => binding.name.text)
  })
}
