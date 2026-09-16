import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })
const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

function ownAttrs(src: string, start: number): string {
  let depth = 0, out = ''
  for (let i = start; i < src.length; i++) {
    const c = src[i]
    if (c === '{') { depth++; continue }
    if (c === '}') { depth--; continue }
    if (depth === 0) {
      if (c === '>') return out
      out += c
    }
  }
  return out
}

function appComponents(): Map<string, string> {
  const defs = new Map<string, string>()
  for (const f of walk(SRC)) {
    const src = readFileSync(f, 'utf8')
    for (const m of src.matchAll(/export function ([A-Z]\w*)\s*\(/g)) defs.set(m[1], f.slice(SRC.length + 1))
    for (const m of src.matchAll(/export const ([A-Z]\w*)\s*=\s*(?:memo|forwardRef|\()/g)) defs.set(m[1], f.slice(SRC.length + 1))
  }
  return defs
}

function hyphenatedAriaOnAppComponents() {
  const defs = appComponents()
  const out: { file: string; comp: string; arias: string[]; def: string }[] = []
  for (const f of walk(SRC)) {
    const src = strip(readFileSync(f, 'utf8'))
    for (const m of src.matchAll(/<([A-Z][\w.]*)\b/g)) {
      const def = defs.get(m[1])
      if (!def) continue
      const arias = [...ownAttrs(src, m.index! + m[0].length).matchAll(/\s(aria-[a-z]+)=/g)].map((x) => x[1])
      if (!arias.length) continue
      const target = readFileSync(join(SRC, def), 'utf8')
      if (/\.\.\.(rest|props)\b/.test(target)) continue
      if (arias.some((a) => new RegExp(`['"\`]${a}['"\`]\\s*[:?]|\\[['"]${a}['"]\\]`).test(target))) continue
      out.push({ file: f.slice(SRC.length + 1), comp: m[1], arias, def })
    }
  }
  return out
}

describe('a hyphenated aria prop on one of our components is dropped, so nobody writes one', () => {
  it('no call site passes aria-* to an app component that cannot forward it', () => {
    const dropped = hyphenatedAriaOnAppComponents()
      .map((h) => `${h.file}  <${h.comp} ${h.arias.join(' ')}>  → ${h.def} declares camelCase only`)
    expect(dropped, `these compile and reach nothing:\n${dropped.join('\n')}\n\nUse the component's own camelCase prop (ariaLabel / ariaExpanded / ariaPressed).`)
      .toEqual([])
  })

  it('the scan is still looking — floors on both halves', () => {
    const files = walk(SRC)
    expect(files.length, 'the tsx walk must resolve').toBeGreaterThan(200)
    const defs = appComponents()
    expect(defs.size, 'app components must resolve').toBeGreaterThan(150)
    expect(defs.has('Button'), 'shared/ui/Button is the primitive this rail was written about').toBe(true)
    const filterMenu = strip(readFileSync(join(SRC, 'shared/ui/FilterMenu.tsx'), 'utf8'))
    const at = filterMenu.indexOf('<Popover')
    expect(at, 'FilterMenu must still render a Popover').toBeGreaterThan(-1)
    expect(filterMenu.slice(at), 'and still put aria-expanded inside the trigger callback').toMatch(/aria-expanded=/)
    expect(ownAttrs(filterMenu, at + '<Popover'.length), 'which is NOT one of Popover\'s own attributes')
      .not.toMatch(/aria-/)
  })

  it('Button still declares the camelCase surface the fix depends on', () => {
    const src = readFileSync(join(SRC, 'shared/ui/Button.tsx'), 'utf8')
    for (const prop of ['ariaLabel', 'ariaExpanded', 'ariaPressed']) expect(src).toContain(prop)
    expect(src, 'and it renders them onto the button').toMatch(/aria-label=\{ariaLabel\}/)
    expect(src, 'no rest spread — the reason a hyphenated prop vanishes').not.toMatch(/\.\.\.(rest|props)\b/)
  })

  it('the two fixed call sites use the camelCase props', () => {
    const matrix = readFileSync(join(SRC, 'features/settings/NotificationRulesMatrix.tsx'), 'utf8')
    expect(matrix).toMatch(/ariaExpanded=\{isOpen\}/)
    expect(matrix).toMatch(/ariaLabel=\{`\$\{isOpen \? 'Hide' : 'Show'\} delivery detail for \$\{r\.label\}`\}/)
    const audit = readFileSync(join(SRC, 'features/settings/AuditPanel.tsx'), 'utf8')
    expect(audit).toMatch(/ariaExpanded=\{showMore\}/)
  })
})
