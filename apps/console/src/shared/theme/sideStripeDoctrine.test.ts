import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const ROOT = join(process.cwd(), "src")

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(ts|tsx)$/.test(name) && !/\.(test|spec)\./.test(name)) out.push(p)
  }
  return out
}

function isComment(line: string): boolean {
  const t = line.trimStart()
  return t.startsWith('//') || t.startsWith('*') || t.startsWith('/*')
}

function findSideStripes(): string[] {
  const offenders: string[] = []
  for (const file of walk(ROOT)) {
    const lines = readFileSync(file, 'utf8').split('\n')
    lines.forEach((line, i) => {
      if (isComment(line)) return
      if (/border-l-\[[2-9]\d*px\]/.test(line)) offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1} ${line.trim()}`)
      if (/\bborderLeft\s*:\s*['"`]?\s*[2-9]\d*px/.test(line)) offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1} ${line.trim()}`)
      if (/inset\s+-?[2-9]\d*px\s+0(px)?\s+0(px)?/.test(line) || /shadow-\[inset_-?[2-9]\d*px_0/.test(line)) offenders.push(`${file.slice(ROOT.length + 1)}:${i + 1} ${line.trim()}`)
    })
  }
  return offenders
}

describe('side-stripe doctrine (Tone-Not-Line: no colored side-stripes, ever)', () => {
  it('no multi-pixel border-left utility or inline borderLeft style ships in src', () => {
    const offenders = findSideStripes()
    expect(
      offenders,
      `Colored side-stripe(s) detected — web/DESIGN.md's Tone-Not-Line rule bans border-left ` +
        `wider than the 1px hairline. Carry the tone in a chip, dot, or icon instead ` +
        `(see the Code Cockpit toast: icon + Respond button already carry it).\n` +
        offenders.join('\n'),
    ).toEqual([])
  })
})
