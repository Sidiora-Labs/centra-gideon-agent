import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

const read = (file: string) => readFileSync(join(process.cwd(), `src/features/knowledge/${file}`), 'utf8')

describe('shelf chips keep long names recoverable', () => {
  it.each([
    ['KnowledgeListPage.tsx', /<span className="max-w-48 truncate" title=\{c\.name\}>\{c\.name\}<\/span>/],
    ['LibraryHome.tsx', /<span className="max-w-48 truncate" title=\{c\.name \|\| 'Untitled shelf'\}>\{c\.name \|\| 'Untitled shelf'\}<\/span>/],
  ])('%s truncates the visible name and exposes its full value', (file, shelfChip) => {
    expect(read(file)).toMatch(shelfChip)
  })
})
