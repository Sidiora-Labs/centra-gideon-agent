
import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { WRITE_ENV } from './consistencyAudit.generate.test'

const SRC = join(process.cwd(), "src")

const FS_WRITE = /\b(writeFileSync|appendFileSync|mkdirSync|rmSync|rmdirSync|unlinkSync|cpSync|copyFileSync|renameSync|writeFile|createWriteStream)\s*\(/

const MAY_WRITE: Record<string, string> = {
  'shared/theme/consistencyAudit.generate.test.ts':
    'The generator for the committed drift inventory, gated on ' +
    `${WRITE_ENV}=1 and invoked only by \`npm run audit:consistency\`. A plain test ` +
    'run skips it, so the tree stays clean.',
}

function testFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) out.push(...testFiles(p))
    else if (/\.test\.tsx?$/.test(entry)) out.push(p)
  }
  return out
}

describe('no test writes into the repository', () => {
  const files = testFiles(SRC)

  it('found the suite it is supposed to be scanning', () => {
    expect(files.length).toBeGreaterThan(100)
    expect(files.some((f) => f.endsWith('consistencyAudit.test.ts'))).toBe(true)
    expect(FS_WRITE.test('writeFileSync' + '(out, data)')).toBe(true)
    expect(FS_WRITE.test('mkdir' + 'Sync(dir)')).toBe(true)
    expect(FS_WRITE.test('const x = readFileSync(p)')).toBe(false)
    expect(FS_WRITE.test('readdirSync(dir)')).toBe(false)
  })

  it('no unlisted test file calls a mutating fs API', () => {
    const offenders = files
      .filter((f) => FS_WRITE.test(readFileSync(f, 'utf8')))
      .map((f) => relative(SRC, f).split(/[\\/]/).join('/'))
      .filter((rel) => !(rel in MAY_WRITE))
    expect(offenders).toEqual([])
  })

  it('every allowlisted writer is gated so a plain test run cannot fire it', () => {
    for (const [rel, reason] of Object.entries(MAY_WRITE)) {
      expect(reason.trim().length, `${rel} needs a real reason`).toBeGreaterThan(20)
      const source = readFileSync(join(SRC, rel), 'utf8')
      expect(FS_WRITE.test(source), `${rel} no longer writes — delete its row`).toBe(true)
      expect(source.includes(`process.env[WRITE_ENV]`) || source.includes(WRITE_ENV)).toBe(true)
      expect(source).toMatch(/it\.runIf\(/)
    }
  })

  it('the plain reporter test is not the writer any more', () => {
    const source = readFileSync(join(SRC, 'shared/theme/consistencyAudit.test.ts'), 'utf8')
    expect(FS_WRITE.test(source)).toBe(false)
  })
})
