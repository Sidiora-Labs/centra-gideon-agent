import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(process.cwd(), "src/features")

function pageFiles(): string[] {
  const out: string[] = []
  const walk = (dir: string) => {
    for (const e of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, e.name)
      if (e.isDirectory()) { walk(p); continue }
      if (/\.tsx$/.test(e.name) && !/\.test\.tsx$/.test(e.name)) out.push(p)
    }
  }
  walk(PAGES)
  return out
}

function rightSlots(src: string): Array<{ line: number; body: string }> {
  const out: Array<{ line: number; body: string }> = []
  for (const m of src.matchAll(/right=\{/g)) {
    const open = m.index! + m[0].length - 1
    let depth = 0
    let end = open
    for (let i = open; i < Math.min(src.length, open + 4000); i++) {
      if (src[i] === '{') depth++
      else if (src[i] === '}') { depth--; if (depth === 0) { end = i; break } }
    }
    out.push({ line: src.slice(0, m.index!).split('\n').length, body: src.slice(open, end) })
  }
  return out
}

const CONTROL = /<(Button|IconButton|SquareIconButton|QuietButton|Segmented|FilterMenu|Popover|Checkbox)\b/g

const EXEMPT: Record<string, string> = {
  'workflows/WorkflowRunDetail.tsx':
    '5 declared, at most 4 rendered (Steer/Pause/Cancel are mid-run only; a terminal run shows ' +
    'Workspace + Fork). Measured on a terminal run at 390px: 1px overflow, 0 unreachable. The ' +
    '4-control mid-run branch needs a live running workflow to observe — logged, not converted.',
}

describe('header right slots use the responsive cluster', () => {
  const files = pageFiles()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(files.length).toBeGreaterThan(40)
    expect(files.some((f) => f.includes('KnowledgeListPage'))).toBe(true)
  })

  it('every exemption is still real (a stale waiver silently widens the rail)', () => {
    for (const rel of Object.keys(EXEMPT)) {
      const f = join(PAGES, rel)
      expect(files, `exempt file ${rel} no longer exists — drop the entry`).toContain(f)
      const src = readFileSync(f, 'utf8')
      const slots = rightSlots(src).filter((s) => !s.body.includes('HeaderActions'))
      expect(
        slots.some((s) => [...s.body.matchAll(CONTROL)].length >= 2),
        `${rel} no longer has a hand-rolled multi-control slot — remove it from EXEMPT`,
      ).toBe(true)
    }
  })

  it('every multi-control right slot goes through HeaderActions', () => {
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(f, 'utf8')
      if (!src.includes('right={')) continue
      const rel = f.slice(PAGES.length + 1)
      if (rel in EXEMPT) continue
      for (const { line, body } of rightSlots(src)) {
        if (body.includes('HeaderActions')) continue
        const n = [...body.matchAll(CONTROL)].length
        if (n < 2) continue
        offenders.push(`${rel}:${line} — ${n} controls, no HeaderActions`)
      }
    }
    expect(
      offenders,
      'A hand-rolled multi-control header row does not shed labels or overflow into a `…` ' +
        'menu, and TopBar\'s right slot is shrink-0 — so it runs off the edge and takes the ' +
        `page title's width with it.\n${offenders.join('\n')}`,
    ).toEqual([])
  })
})
