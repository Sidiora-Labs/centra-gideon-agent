import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const THRESHOLD = 10
const SRC = join(process.cwd(), "src")

function walk(dir: string, out: string[] = []): string[] {
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.tsx?$/.test(p) && !/\.test\.tsx?$/.test(p)) out.push(p)
  }
  return out
}

function registrySizes(files: string[]): Map<string, number> {
  const sizes = new Map<string, number>()
  for (const f of files) {
    const s = readFileSync(f, 'utf8')
    const re = /(?:export )?const ([A-Za-z_][\w]*)(?::[^=]+)? = \[/g
    let m: RegExpExecArray | null
    while ((m = re.exec(s))) {
      const open = m.index + m[0].length - 1
      let depth = 0, end = open
      for (let i = open; i < s.length; i++) {
        if (s[i] === '[') depth++
        else if (s[i] === ']') { depth--; if (!depth) { end = i; break } }
      }
      const body = s.slice(open, end + 1)
      const n = (body.match(/\{\s*(?:key|id)\s*:/g) ?? []).length
      if (n > (sizes.get(m[1]) ?? 0)) sizes.set(m[1], n)
    }
  }
  return sizes
}

function segmentedSites(files: string[]) {
  const sites: { file: string; line: number; collapse: string | null; registries: string[] }[] = []
  for (const f of files) {
    const s = readFileSync(f, 'utf8')
    let i = 0
    while ((i = s.indexOf('<Segmented', i)) !== -1) {
      let depth = 0, end = i
      for (let j = i; j < s.length; j++) {
        const c = s[j]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0 && s[j - 1] === '/') { end = j; break }
      }
      const tag = s.slice(i, end + 1)
      sites.push({
        file: f.slice(SRC.length + 1),
        line: s.slice(0, i).split('\n').length,
        collapse: (tag.match(/collapse="(\w+)"/) ?? [])[1] ?? null,
        registries: [...tag.matchAll(/\b([A-Z][A-Z0-9_]{2,})\.map\(/g)].map((m) => m[1]),
      })
      i = end + 1
    }
  }
  return sites
}

const FILES = walk(SRC)
const SIZES = registrySizes(FILES)
const SITES = segmentedSites(FILES)

describe('a Segmented fed by a large registry declares how it collapses', () => {
  it('the scan found call sites and registries at all', () => {
    expect(SITES.length).toBeGreaterThan(20)
    expect(SIZES.get('ARTIFACT_KINDS')).toBe(16)
  })

  it(`at least one site is actually governed by the >=${THRESHOLD}-option rule`, () => {
    const governed = SITES.filter((s) => s.registries.some((r) => (SIZES.get(r) ?? 0) >= THRESHOLD))
    expect(governed.length).toBeGreaterThan(0)
  })

  it(`every Segmented mapping a registry of >=${THRESHOLD} options passes \`collapse\``, () => {
    const offenders = SITES
      .filter((s) => s.registries.some((r) => (SIZES.get(r) ?? 0) >= THRESHOLD))
      .filter((s) => !s.collapse)
      .map((s) => {
        const big = s.registries.map((r) => `${r}=${SIZES.get(r)}`).join(',')
        return `${s.file}:${s.line} (${big})`
      })
    expect(offenders, 'a strip this wide is cut off, not shrunk — pass collapse="scroll" or "menu"').toEqual([])
  })

  it('the artifacts kind filter — the site this rail was measured on — scrolls', () => {
    const site = SITES.find((s) => s.file === 'features/artifacts/ArtifactsSection.tsx' && s.registries.includes('ARTIFACT_KINDS'))
    expect(site, 'the artifacts kind Segmented moved or stopped mapping ARTIFACT_KINDS').toBeTruthy()
    expect(site!.collapse).toBe('scroll')
  })
})
