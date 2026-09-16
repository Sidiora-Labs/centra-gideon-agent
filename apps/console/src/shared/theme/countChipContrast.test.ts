import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const WEB_SRC = join(process.cwd(), "src")

function classAttributes(): Array<{ file: string; line: number; value: string }> {
  const out: Array<{ file: string; line: number; value: string }> = []
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const p = join(dir, entry.name)
      if (entry.isDirectory()) { walk(p); continue }
      if (!/\.tsx$/.test(entry.name) || /\.test\.tsx$/.test(entry.name)) continue
      readFileSync(p, 'utf8').split('\n').forEach((ln, i) => {
        for (const m of ln.matchAll(/className="([^"]*)"/g)) {
          out.push({ file: p.slice(WEB_SRC.length + 1), line: i + 1, value: m[1] })
        }
      })
    }
  }
  walk(WEB_SRC)
  return out
}

const DIMS = /\bopacity-(40|50|60|70|75|80)\b/
const COUNT_ISH = /\btabular-nums\b/

describe('count chips do not double-dim a dimmed token', () => {
  const attrs = classAttributes()

  it('scans a real tree (guards against a silently-empty sweep)', () => {
    expect(attrs.length).toBeGreaterThan(500)
  })

  it('no tabular-nums count carries an opacity-* dimmer', () => {
    const offenders = attrs.filter((a) => COUNT_ISH.test(a.value) && DIMS.test(a.value))
      .map((a) => `${a.file}:${a.line} — ${a.value.slice(0, 90)}`)
    expect(offenders, `A count chip inherits its chip's already-dimmed colour; opacity halves it again.\n${offenders.join('\n')}`).toEqual([])
  })
})
