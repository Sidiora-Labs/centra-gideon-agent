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

function tags(src: string, name: string): string[] {
  const out: string[] = []
  const re = new RegExp(`<${name}(?=[\\s>])`, 'g')
  let m: RegExpExecArray | null
  while ((m = re.exec(src))) {
    let i = m.index + 1 + name.length, depth = 0, quote: string | null = null
    for (; i < src.length; i++) {
      const c = src[i]
      if (quote) { if (c === quote) quote = null; continue }
      if (c === '"' || c === "'" || c === '`') { quote = c; continue }
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) break
    }
    out.push(src.slice(m.index, i + 1))
  }
  return out
}

const pickers = () => walk(SRC).flatMap((abs) => {
  const src = readFileSync(abs, 'utf8')
  return tags(src, 'input')
    .filter((t) => /type="file"/.test(t))
    .map((tag) => ({ file: abs.slice(SRC.length + 1), tag, src }))
})

const FORWARDED: Record<string, RegExp> = {
  'features/loops/DesignCockpitPage.tsx': /<Button[^>]*onClick=\{\(\) => fileRef\.current\?\.click\(\)\}/,
  'features/settings/PortabilityPanel.tsx': /<Button[^>]*onClick=\{\(\) => fileRef\.current\?\.click\(\)\}/,
  'features/files/browse/FileTree.tsx': /label: 'Upload here', onClick: \(\) => uploadInput\.current\?\.click\(\)/,
  'shared/ui/Composer.tsx': /onAttach=\{\(\) => fileRef\.current\?\.click\(\)\}/,
}

describe('every file picker can be reached without a mouse', () => {
  it('finds the population (not vacuously green)', () => {
    expect(pickers().length, 'the file-input census must not go empty').toBeGreaterThanOrEqual(7)
  })

  it('is either focusable itself, or forwarded to by a named real control', () => {
    const bad: string[] = []
    for (const { file, tag, src } of pickers()) {
      const focusable = /className="sr-only"|className={`sr-only/.test(tag) || /className="sr-only"/.test(tag)
      if (focusable) continue
      const fwd = FORWARDED[file]
      if (fwd && fwd.test(src)) continue
      bad.push(`${file}: hidden picker with no focusable input and no named forwarding control`)
    }
    expect(bad, `a file picker is pointer-only:\n${bad.join('\n')}`).toEqual([])
  })

  it('the two fixed this cycle keep their focusable input AND a visible focus ring', () => {
    const cases: [string, RegExp][] = [
      ['features/knowledge/KnowledgeCreatePage.tsx', /border-dashed[\s\S]{0,200}?has-\[input:focus-visible\]:ring-2/],
      ['features/loop/LoopComposer.tsx', /<label[\s\S]{0,300}?has-\[input:focus-visible\]:ring-2/],
    ]
    for (const [rel, ring] of cases) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(code, `${rel}: the picker must stay focusable`).toMatch(/type="file"[\s\S]{0,220}?className="sr-only"/)
      expect(code, `${rel}: a visually hidden input needs the ring drawn on its container`).toMatch(ring)
      expect(code, `${rel}: the input must sit inside the element carrying the ring`)
        .toMatch(/has-\[input:focus-visible\]:ring-primary\b[\s\S]{0,400}?type="file"/)
    }
  })

  it('the knowledge drop area guards the re-entrant click', () => {
    const code = readFileSync(join(SRC, 'features/knowledge/KnowledgeCreatePage.tsx'), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).toMatch(/if \(e\.target === fileRef\.current\) return/)
  })

  it('the knowledge copy no longer says only "click"', () => {
    const src = readFileSync(join(SRC, 'features/knowledge/KnowledgeCreatePage.tsx'), 'utf8')
    expect(src).not.toMatch(/or click to choose/)
    expect(src).toMatch(/or choose one/)
  })
})
