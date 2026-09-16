import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((n) => {
    const p = join(dir, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('both Monaco consumers name their editing surface', () => {
  it('ContentSurface names the editor from the document title', () => {
    const src = read('shared/ui/content/ContentSurface.tsx')
    expect(src).toMatch(/ariaLabel: `\$\{title\} — editor`/)
  })

  it('GistEditor names the editor from the gist language', () => {
    const src = read('features/knowledge/GistEditor.tsx')
    expect(src).toMatch(/ariaLabel: language \? `Gist content \(\$\{language\}\)` : 'Gist content'/)
  })

  it('GistEditor exposes no ariaLabel PROP — nothing would pass it', () => {
    const src = read('features/knowledge/GistEditor.tsx')
    expect(/ariaLabel\?: string/.test(src), 'no callerless ariaLabel prop').toBe(false)
    for (const rel of ['features/knowledge/KnowledgeCreatePage.tsx', 'features/knowledge/KnowledgeDetail.tsx']) {
      const tag = read(rel).match(/<GistEditor\b[\s\S]*?\/>/)
      expect(tag, `${rel} should mount GistEditor`).toBeTruthy()
      expect(tag![0], `${rel} must pass language so the derived name is specific`).toMatch(/language=\{/)
    }
  })
})

describe('the rail: every Monaco mount names itself specifically', () => {
  it('no file mounts MonacoEditor without an ariaLabel', () => {
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      const code = readFileSync(abs, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '')
        .replace(/^\s*\/\/.*$/gm, '')
      if (!/<MonacoEditor\b/.test(code)) continue
      if (!/ariaLabel:/.test(code)) offenders.push(abs.slice(SRC.length + 1))
    }
    expect(
      offenders,
      `These files mount MonacoEditor with no ariaLabel, so the editor falls back to Monaco's ` +
        `generic "Editor content" and announces the same name as every other editor:\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the rail actually finds the Monaco mounts (it is not vacuously green)', () => {
    const mounting = walk(SRC).filter((abs) => /<MonacoEditor\b/.test(readFileSync(abs, 'utf8')))
      .map((abs) => abs.slice(SRC.length + 1)).sort()
    expect(mounting).toEqual(['features/knowledge/GistEditor.tsx', 'shared/ui/content/ContentSurface.tsx'])
  })
})
