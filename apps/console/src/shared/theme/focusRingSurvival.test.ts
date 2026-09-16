import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'


const SRC = join(process.cwd(), "src")

const OUTLINE_KILLED = /outline-none|outline:\s*none|outline:\s*0\b/

const FOCUS_TREATMENT =
  /focus-visible|:focus-visible|focus-within|focus:ring|focus:border|focus:bg|focus:outline|ring-\d|ring-\[|ring-offset|box-shadow|focus:shadow|data-\[focus|has-\[/

const BASELINE = 2

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(ts|tsx|css)$/.test(name)) out.push(p)
  }
  return out
}

function withoutComments(text: string): string {
  return text
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^[ \t]*\/\/.*$/gm, (m) => ' '.repeat(m.length))
}

function unringedSites(): string[] {
  const hits: string[] = []
  for (const file of walk(SRC)) {
    const rel = relative(SRC, file).replace(/\\/g, '/')
    if (rel.startsWith('shared/theme/') || rel.includes('.test.')) continue
    const text = withoutComments(readFileSync(file, 'utf8'))
    if (!OUTLINE_KILLED.test(text)) continue
    if (FOCUS_TREATMENT.test(text)) continue
    text.split('\n').forEach((line, i) => {
      if (OUTLINE_KILLED.test(line)) hits.push(`${rel}:${i + 1}`)
    })
  }
  return hits
}

describe('the global focus ring must survive `outline-none`', () => {
  it('a comment that NAMES the utility is not counted as a site', () => {
    const jsx = '{/* we deliberately set outline-none here */}\nconst a = 1\n'
    const line = '  // outline: none is fine on this one\nconst b = 2\n'
    expect(withoutComments(jsx)).not.toMatch(OUTLINE_KILLED)
    expect(withoutComments(line)).not.toMatch(OUTLINE_KILLED)
    expect(withoutComments(jsx).split('\n').length).toBe(jsx.split('\n').length)
  })

  it('a trailing comment is left alone, so a URL cannot be mangled', () => {
    const code = 'const u = "https://example.com/x" // outline-none\n'
    expect(withoutComments(code)).toContain('https://example.com/x')
  })

  it('the detector finds the population it is about (vacuity floor)', () => {
    expect(
      unringedSites().length,
      'the scan found NO outline-none sites at all — the detector broke, it did not get clean',
    ).toBeGreaterThan(0)
  })

  it('tokens.css still provides the one global ring this rail protects', () => {
    const tokens = readFileSync(join(SRC, "shared/theme", 'tokens.css'), 'utf8')
    expect(tokens, 'the global :focus-visible ring is gone — re-derive this rail').toMatch(
      /:focus-visible\s*\{[^}]*outline:/,
    )
  })

  it(`no NEW element kills the focus ring without replacing it (baseline ${BASELINE})`, () => {
    const sites = unringedSites()
    expect(
      sites.length,
      `${sites.length} sites set outline-none in a file with NO focus treatment anywhere, ` +
        `above the baseline of ${BASELINE}. Add a replacement on the same element ` +
        `(focus-visible:ring-2, focus:border-primary) or drop the outline-none and let the global ` +
        `ring paint. New sites:\n  ${sites.slice(BASELINE).join('\n  ')}`,
    ).toBeLessThanOrEqual(BASELINE)
  })
})
