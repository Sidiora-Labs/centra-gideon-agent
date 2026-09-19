import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { ERROR_TREATMENTS } from './errorTreatments'

const ROOT = process.cwd()
const TOKENS_PATH = join(ROOT, 'src/shared/theme/tokens.css')
const ERROR_SURFACES = [
  'src/app/shell/IncidentBanner.tsx',
  'src/shared/theme/errorTreatments.ts',
  'src/features/settings/UpdatesPanel.tsx',
]

function tokenSource(): string {
  return readFileSync(TOKENS_PATH, 'utf8')
}

function tokenValue(name: string): string | null {
  const match = tokenSource().match(new RegExp(`${name}:\\s*([^;]+);`))
  return match?.[1].trim() ?? null
}

describe('the canonical danger token', () => {
  it('backs every error surface role', () => {
    expect(tokenValue('--color-error')).toBe('var(--color-danger)')
    expect(tokenValue('--color-error-container')).toContain('var(--color-danger)')
    expect(tokenValue('--color-on-error-container')).toBe('var(--color-danger)')
    expect(new Set(Object.values(ERROR_TREATMENTS).map((treatment) => treatment.paint.icon)))
      .toEqual(new Set(['--color-danger']))
  })

  it('leaves no undefined color reads on error surfaces', () => {
    const declared = new Set(
      [...tokenSource().matchAll(/(--color-[a-z-]+)\s*:/g)].map((match) => match[1]),
    )
    const undefinedReads = ERROR_SURFACES.flatMap((relativePath) => {
      const source = readFileSync(join(ROOT, relativePath), 'utf8')
      const reads = new Set([...source.matchAll(/--color-[a-z-]+/g)].map((match) => match[0]))
      return [...reads]
        .filter((token) => !declared.has(token))
        .map((token) => `${relativePath}: ${token}`)
    })

    expect(undefinedReads).toEqual([])
  })
})
