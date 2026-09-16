import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const code = (abs: string): string =>
  readFileSync(abs, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^(\s*)\/\/.*$/gm, '$1')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const BUSY = /\b(busy|saving|sending|loading|installing|retrying|pending|working|submitting|launching|testing|promoting|consolidating|regen\w*|bulkBusy|levelBusy|deleting|creating|running|uploading|importing|exporting|refreshing|syncing|starting|stopping|genning|repairing|reloading|applying|generating|fetching|polling|checking)\b/i

const BARE = /^\s*!*\s*(busy|bulkBusy|levelBusy|pending|loading|saving|working)\s*$/

function elements(src: string): Array<{ tag: string; body: string; line: number }> {
  const out: Array<{ tag: string; body: string; line: number }> = []
  for (const m of src.matchAll(/<Button\b/g)) {
    let depth = 0
    let tagEnd = -1
    let selfClose = false
    for (let i = m.index! + m[0].length; i < src.length; i++) {
      const ch = src[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (ch === '>' && depth === 0) { tagEnd = i; selfClose = src[i - 1] === '/'; break }
    }
    if (tagEnd < 0) continue
    const close = selfClose ? -1 : src.indexOf('</Button>', tagEnd)
    out.push({
      tag: src.slice(m.index!, tagEnd + 1),
      body: close < 0 ? '' : src.slice(tagEnd + 1, close),
      line: src.slice(0, m.index!).split('\n').length,
    })
  }
  return out
}

function gateOf(tag: string): string | null {
  const i = tag.search(/(?<!aria-)\bdisabled=\{/)
  if (i < 0) return null
  const start = tag.indexOf('{', i)
  let d = 0
  for (let j = start; j < tag.length; j++) {
    if (tag[j] === '{') d++
    else if (tag[j] === '}') { d--; if (d === 0) return tag.slice(start + 1, j) }
  }
  return null
}

function disjuncts(g: string): string[] {
  const out: string[] = []
  let d = 0
  let last = 0
  for (let i = 0; i < g.length; i++) {
    const ch = g[i]
    if ('([{'.includes(ch)) d++
    else if (')]}'.includes(ch)) d--
    else if (d === 0 && ch === '|' && g[i + 1] === '|') { out.push(g.slice(last, i)); i++; last = i + 1 }
  }
  out.push(g.slice(last))
  return out.map((s) => s.trim()).filter(Boolean)
}

function spinnerCond(body: string): string | null {
  const condBefore = (at: number): string | null => {
    const before = body.slice(0, at)
    const q = before.lastIndexOf('?')
    if (q < 0) return null
    let d = 0
    let start = 0
    for (let j = q - 1; j >= 0; j--) {
      const ch = before[j]
      if (')]}'.includes(ch)) d++
      else if ('([{'.includes(ch)) { if (d === 0) { start = j + 1; break } d-- }
    }
    return before.slice(start, q).trim() || null
  }

  const loader = body.search(/Loader2/)
  if (loader >= 0) {
    const c = condBefore(loader)
    if (c) return c
  }
  const anim = body.match(/([A-Za-z_$][\w$.]*)\s*\?\s*'animate-(?:pulse|spin)'/)
  if (anim) return anim[1].trim()
  const label = body.match(/([A-Za-z_$][\w$.]*)\s*\?\s*'[^']*…'\s*:/)
  if (label) return label[1].trim()
  return null
}

function loadingFlags(src: string): Set<string> {
  const out = new Set<string>()
  for (const m of src.matchAll(/\bloading=\{([^}]{1,80})\}/g)) {
    for (const id of m[1].match(/[A-Za-z_$][\w$]*/g) ?? []) out.add(id)
  }
  return out
}

const norm = (s: string) => s.replace(/\s+/g, ' ').trim()

type Site = { at: string; gate: string; spinner: string | null }
const census = () => {
  const announced: Site[] = []
  const A: Site[] = []
  const B: Site[] = []
  const C: Site[] = []
  const D: Site[] = []
  let population = 0
  for (const abs of walk(SRC)) {
    const rel = abs.slice(SRC.length + 1)
    const src = code(abs)
    const published = loadingFlags(src)
    for (const { tag, body, line } of elements(src)) {
      const gate = gateOf(tag)
      if (gate === null) continue
      population++
      if (!BUSY.test(gate)) continue
      const site: Site = { at: `${rel}:${line}`, gate: norm(gate), spinner: spinnerCond(body) }
      if (/aria-busy|\bloading=/.test(tag)) { announced.push(site); continue }
      const ds = disjuncts(gate)
      const busyDs = ds.filter((d) => BUSY.test(d))
      const ownFlag = gate.match(/[A-Za-z_$][\w$]*/)?.[0]
      const bystander = !site.spinner && !!ownFlag && published.has(ownFlag)
      if (ds.length === 1 && site.spinner && norm(site.spinner) === norm(gate)) A.push(site)
      else if (ds.length > 1 && busyDs.length === 1 && BARE.test(busyDs[0])) D.push(site)
      else if (ds.length > 1) C.push(site)
      else if (BARE.test(gate) || bystander) D.push(site)
      else B.push(site)
    }
  }
  return { population, announced, A, B, C, D }
}

describe('the `aria-busy` exemption is measured, not asserted by comment', () => {
  it('🔑 THE PREMISE: Button publishes aria-busy from `loading`, NOT from `disabled`', () => {
    const btn = code(join(SRC, "shared/ui", 'Button.tsx'))
    expect(btn, 'aria-busy comes from loading').toMatch(/aria-busy=\{loading \|\| undefined\}/)
    expect(btn, 'and NOT from the disabled gate').not.toMatch(/aria-busy=\{[^}]*\bdisabled\b/)
    expect(btn, '`loading` and `disabled` are independent inputs to one off-state')
      .toMatch(/const off = !!disabled \|\| loading/)
  })

  it('🔴 the two rails no longer claim the exemption they never checked', () => {
    const triage = readFileSync(join(SRC, "shared/ui", 'disabledReasonTriage.test.ts'), 'utf8')
    expect(triage, 'the false exemption criterion is gone').not.toMatch(/aria-busy` already announces/)
    expect(triage, 'and it says what is actually true instead').toMatch(/announces nothing|no `aria-busy`|NOT announced/)
    const raw = readFileSync(join(SRC, "shared/ui", 'rawSoftOffContract.test.ts'), 'utf8')
    expect(raw, 'the false exemption criterion is gone').not.toMatch(/`aria-busy` already says so/)
    expect(raw, 'and it says what is actually true instead').toMatch(/announces nothing|no `aria-busy`|NOT announced/)
  })

  it('finds the population it is filtering (not vacuously green)', () => {
    const { population, announced, A, B, C, D } = census()
    expect(population, 'the matcher must find the disabled Buttons').toBeGreaterThanOrEqual(150)
    const busyGated = announced.length + A.length + B.length + C.length + D.length
    expect(busyGated, 'and the busy-gated subset the exemption covers').toBeGreaterThanOrEqual(100)
  })

  it('🔴 THE RATCHET: the number of busy-gated Buttons announcing nothing may only go DOWN', () => {
    const { A, B, C, D } = census()
    const unannounced = [...A, ...B, ...C, ...D]
    expect(
      unannounced.length,
      'a busy-gated Button that announces nothing to assistive tech:\n  ' +
        unannounced.slice(0, 12).map((s) => `${s.at}  disabled={${s.gate}}`).join('\n  ') +
        `\n  …and ${Math.max(0, unannounced.length - 12)} more`,
    ).toBeLessThanOrEqual(79)
  })

  it('records the classes, because they want OPPOSITE fixes', () => {
    const { A, B, C, D } = census()
    expect(A, 'a hand-rolled spinner whose condition IS the disabled gate — use `loading=` instead')
      .toEqual([])
    expect(C.length, 'Class C — mixed gate; `loading` takes the busy disjunct, `disabled` keeps the gate')
      .toBeGreaterThanOrEqual(3)
    expect(D.length, 'Class D — bystander shared flag with NO spinner to derive identity from')
      .toBeGreaterThanOrEqual(60)
    expect(D.length, 'the bystanders outnumber the genuine cases — a sweep would have lied at most sites')
      .toBeGreaterThan(A.length + B.length + C.length)
  })

  it('🪤 the spinner condition is NOT always the gate — the trap a codemod would fall into', () => {
    const { A, B, C, D } = census()
    const mismatched = [...A, ...B, ...C, ...D]
      .filter((s) => s.spinner && norm(s.spinner) !== norm(s.gate))
      .map((s) => s.at.split(':')[0])
    expect([...new Set(mismatched)], 'a new shared-gate/narrow-spinner site needs the two-prop fix')
      .toEqual(['features/schedule/ScheduleDetail.tsx'])
  })

  it('the sites that DO announce it keep doing so', () => {
    const { announced } = census()
    expect(announced.length, 'buttons that publish their in-flight state').toBeGreaterThanOrEqual(6)
  })
})

describe('a slow action can name what it is doing AND announce it', () => {
  const VERBS: Array<[string, string]> = [
    ['features/settings/MemoryPanel.tsx', 'Dreaming…'],
    ['features/settings/MemoryPanel.tsx', 'Linking…'],
    ['features/settings/MemoryPanel.tsx', 'Rendering…'],
    ['features/settings/MemoryPanel.tsx', 'Consolidating…'],
    ['features/settings/MemoryPanel.tsx', 'Building…'],
    ['features/settings/MemoryPanel.tsx', 'Syncing…'],
    ['features/settings/AuditPanel.tsx', 'Loading'],
    ['features/tools/ToolInspector.tsx', 'Running…'],
  ]

  it('Button accepts a loading label and renders it beside the spinner', () => {
    const btn = code(join(SRC, "shared/ui", 'Button.tsx'))
    expect(btn, 'the prop exists').toMatch(/loadingLabel\?: string/)
    expect(btn, 'and is destructured, not just declared').toMatch(/loading = false, loadingLabel,/)
    expect(btn, 'rendered only when given, so every existing caller is unaffected')
      .toMatch(/\{loadingLabel \? \(/)
    expect(btn, 'a long verb truncates rather than overflowing the pill it is positioned over')
      .toMatch(/min-w-0 items-center gap-s/)
  })

  it('🔴 a labelled busy state is NOT dimmed to 40% — the word has to be readable', () => {
    const btn = code(join(SRC, "shared/ui", 'Button.tsx'))
    expect(btn, 'the labelled case drops the dim and keeps the click refusal')
      .toMatch(/loading && loadingLabel \? 'disabled:pointer-events-none'/)
    expect(btn, 'and the bare case still dims exactly as before')
      .toMatch(/: 'disabled:opacity-40 disabled:pointer-events-none'/)
    expect(btn, 'no competing opacity utility was added instead').not.toMatch(/disabled:opacity-100/)
  })

  it('🔴 no Button renders a JS expression as literal TEXT — tsc cannot see this', () => {
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      const src = code(abs)
      for (const m of src.matchAll(/<Button\b/g)) {
        let d = 0
        let te = -1
        for (let i = m.index! + m[0].length; i < src.length; i++) {
          const ch = src[i]
          if (ch === '{') d++
          else if (ch === '}') d--
          else if (ch === '>' && d === 0) { te = i + 1; break }
        }
        if (te < 0 || src[te - 2] === '/') continue
        const close = src.indexOf('</Button>', te)
        if (close < 0) continue
        const text = src.slice(te, close)
          .replace(/\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}/g, ' ')
          .replace(/<[^>]*>/g, ' ')
        const at = `${abs.slice(SRC.length + 1)}:${src.slice(0, te).split('\n').length}`
        const prose = text.replace(/\b[\w$]+\.(md|json|ts|tsx|js|jsx|py|txt|ya?ml|sh|css|html|svg|png|toml|lock)\b/gi, ' ')
        if (/\b(null|undefined|NaN)\b/.test(prose)) offenders.push(`${at}  literal keyword as text`)
        else if (/[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*/.test(prose)) offenders.push(`${at}  member expression as text`)
      }
    }
    expect(offenders, 'a Button label showing source instead of its value:\n  ' + offenders.join('\n  '))
      .toEqual([])
  })

  it('🪤 the overlay stays aria-hidden — the accessible name must remain the ACTION', () => {
    const btn = code(join(SRC, "shared/ui", 'Button.tsx'))
    const overlay = btn.slice(btn.indexOf('{loading && ('), btn.indexOf('</AnimatePresence>'))
    expect(overlay, 'the loading overlay must be decorative').toMatch(/aria-hidden/)
    expect(overlay, 'and must not become a label').not.toMatch(/aria-label|sr-only/)
  })

  it('🔴 every progress verb survived the conversion', () => {
    for (const [rel, verb] of VERBS) {
      const src = code(join(SRC, rel))
      expect(src, `${rel} must still say "${verb}"`).toContain(`loadingLabel="${verb}"`)
    }
  })

  it('and none of them kept the hand-rolled fragment that cost them the announcement', () => {
    for (const rel of [...new Set(VERBS.map(([r]) => r))]) {
      const src = code(join(SRC, rel))
      expect(src, `${rel} must not re-grow a manual spinner+verb`).not.toMatch(
        /\?\s*<>\s*<Loader2[^>]*\/>\s*\w/,
      )
    }
  })
})
