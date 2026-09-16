import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC_DIR = join(process.cwd(), "src")
const SRC = readFileSync(join(SRC_DIR, 'features/knowledge/KnowledgeListPage.tsx'), 'utf8')
const CODE = SRC.replace(/\{\/\*[\s\S]*?\*\/\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '')

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const abs = join(dir, name)
    if (statSync(abs).isDirectory()) walk(abs, out)
    else if (/\.tsx$/.test(name) && !name.includes('.test.')) out.push(abs)
  }
  return out
}

describe("an intent's goal survives truncation", () => {
  it('the truncating goal carries its full text in a title', () => {
    expect(CODE).toMatch(
      /<span data-type="body-m" className="truncate text-on-surface" title=\{it\.goal \|\| it\.id\}>\{it\.goal \|\| it\.id\}<\/span>/,
    )
  })

  it('the title and the visible text are the same expression', () => {
    const m = /title=\{([^}]+)\}>\{([^}]+)\}<\/span>/.exec(CODE)
    expect(m, 'the pair is readable from source').toBeTruthy()
    expect(m![1].trim(), 'title matches the rendered expression').toBe(m![2].trim())
  })

  it('the row still hands the FULL goal to assistive tech — the half that already worked', () => {
    expect(CODE).toMatch(/<ListRow[\s\S]{0,200}label=\{it\.goal \|\| it\.id\}/)
  })

  it('and the delete control still names itself from the capped goal', () => {
    expect(CODE).toMatch(/ariaLabel=\{`Delete intent: \$\{rowSubject\(\[it\.goal \|\| it\.id\], 40\)\}`\}/)
  })
})

describe('the population this deliberately does NOT sweep', () => {
  it('is counted, so the next cycle starts from a number', () => {
    const tag = /<(?:span|div|p|h[1-6])\b([^>]*\bclassName="[^"]*\btruncate\b[^"]*"[^>]*)>/g
    let withTitle = 0, without = 0
    for (const abs of walk(SRC_DIR)) {
      for (const m of readFileSync(abs, 'utf8').matchAll(tag)) {
        if (/title=|aria-label/.test(m[1])) withTitle++
        else without++
      }
    }
    expect(withTitle, 'the idiom is real and in use').toBeGreaterThanOrEqual(15)
    expect(without, 'and the untitled population is large enough to need its own measured pass')
      .toBeGreaterThan(100)
  })
})
