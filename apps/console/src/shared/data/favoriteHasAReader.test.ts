
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = join(process.cwd(), "src")
const PAGES = join(SRC, "features")

const FAVORITE_WRITE = /\bapi\.[A-Za-z]*[Ff]avorit[A-Za-z]*\s*\(/

const STAR_NAME = 'aria-label="Favorited"'

function walk(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

function areasThatWrite(): Map<string, string[]> {
  const byArea = new Map<string, string[]>()
  for (const file of walk(PAGES)) {
    if (!FAVORITE_WRITE.test(readFileSync(file, 'utf8'))) continue
    const rel = relative(PAGES, file).split(/[\\/]/)
    const area = rel[0]
    byArea.set(area, [...(byArea.get(area) ?? []), file])
  }
  return byArea
}

const areaSource = (area: string) =>
  walk(join(PAGES, area)).map((f) => readFileSync(f, 'utf8')).join('\n')

describe('a favorite write implies a favorite read', () => {
  const writers = areasThatWrite()

  it('found the writers it is supposed to be checking', () => {
    expect([...writers.keys()].sort()).toEqual(['inbox', 'knowledge'])
    expect(FAVORITE_WRITE.test('api.favoriteInboxItem(item.id, true)')).toBe(true)
    expect(FAVORITE_WRITE.test('api.setKnowledgeFavorited(it.id, next)')).toBe(true)
    expect(FAVORITE_WRITE.test('api.listInboxItems()')).toBe(false)
  })

  it.each([...areasThatWrite().keys()])('%s shows a favorited row in its list', (area) => {
    const source = areaSource(area)
    expect(source).toContain(STAR_NAME)
  })

  it.each([...areasThatWrite().keys()])('%s lets you filter to favorites', (area) => {
    const source = areaSource(area)
    expect(source, 'no favorites predicate/key').toMatch(/'favorites'/)
    expect(source, 'no favorites count').toMatch(/favorited\)\s*\.length|filterCount\('favorites'\)|counts\.favorites/)
    expect(source, 'no selectable Favorites control').toMatch(/\bFavorites\b/)
  })

  it('uses ONE accessible name for the FAVORITED star, everywhere', () => {
    const INDICATOR = /favorited\s*&&\s*<Star\b/
    const names: string[] = []
    for (const file of walk(PAGES)) {
      for (const line of readFileSync(file, 'utf8').split('\n')) {
        if (!INDICATOR.test(line)) continue
        const m = line.match(/aria-label="([^"]+)"/)
        names.push(m ? m[1] : '(indicator with no accessible name)')
      }
    }
    expect(names.length).toBeGreaterThan(1)
    expect([...new Set(names)]).toEqual(['Favorited'])
  })
})
