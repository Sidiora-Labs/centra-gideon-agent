import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(process.cwd(), "src/features")
const SETTINGS = join(PAGES, 'settings')

const REPORTERS: Array<[string, string]> = [
  [join('settings', 'ChatPanel.tsx'), 'saveDashboardConfig'],
  [join('settings', 'InboxSettingsPanel.tsx'), 'saveInboxSettings'],
  [join('settings', 'MemoryPanel.tsx'), 'saveMemorySettings'],
  [join('settings', 'NotificationsPanel.tsx'), 'saveNotificationSettings'],
  [join('inbox', 'InboxSettingsPanel.tsx'), 'saveInboxSettings'],
]

describe('an optimistic settings write reports its failure', () => {
  it.each(REPORTERS)('%s reports a failed %s', (rel, call) => {
    const src = readFileSync(join(PAGES, rel), 'utf8')
    expect(src.includes(`api.${call}(`), `${rel} must still perform the write`).toBe(true)
    const at = src.indexOf(`api.${call}(`)
    const chain = src.slice(at, at + 420)
    expect(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/.test(chain), `${rel}: a silent catch leaves the control lying`).toBe(false)
    expect(/\.catch\(\((?:e|err|error)\)\s*=>/.test(chain), `${rel}: the rejection must be captured`).toBe(true)
    expect(/notify\(/.test(chain), `${rel}: and reported with notify(), like the sibling panels`).toBe(true)
  })

  it('every reporter says "Couldn\'t save" and includes the server message', () => {
    for (const [rel] of REPORTERS) {
      const src = readFileSync(join(PAGES, rel), 'utf8')
      expect(src, `${rel}: the copy must match the family`).toMatch(/Couldn't save/)
      expect(src, `${rel}: a bare "it failed" is not actionable`).toMatch(/\(e as Error\)\?\.message/)
    }
  })

  const ALLOWED_SWALLOWS: Array<[string, string, string]> = [
    ['AccountPanel.tsx', 'dashboardConfig', 'a READ — best-effort hydrate, the panel renders without it'],
    ['AccountPanel.tsx', 'gideonConfig', 'a READ — the panel falls back to defaults when absent'],
    ['AccountPanel.tsx', 'authSession', 'a READ — an absent session renders the signed-out state'],
    ['DiagnosticsPanel.tsx', 'logLevel', 'a READ — the control renders empty until it resolves'],
  ]

  it('no settings panel silently swallows ANY api rejection — allowlist or fix', () => {
    const files = readdirSync(SETTINGS).filter((f) => /\.tsx$/.test(f) && !/\.test\./.test(f))
    expect(files.length, 'the settings panels must be discoverable').toBeGreaterThan(20)
    const offenders: string[] = []
    for (const f of files) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      const scan = src.replace(/=>/g, '\u21d2')
      for (const m of scan.matchAll(/api\.(\w+)\(/g)) {
        const chain = scan.slice(m.index!, m.index! + 420)
        if (!/\.catch\(\s*\(\s*\)\s*\u21d2\s*\{\s*\}\s*\)/.test(chain)) continue
        if (ALLOWED_SWALLOWS.some(([file, call]) => file === f && call === m[1])) continue
        offenders.push(`${f}:${scan.slice(0, m.index).split('\n').length} (api.${m[1]})`)
      }
    }
    expect(offenders, 'a discarded rejection tells the user nothing — report it or allowlist it with a reason').toEqual([])
  })

  it('the allowlist is not a dumping ground — every entry still exists and is still a read', () => {
    for (const [f, call, why] of ALLOWED_SWALLOWS) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      expect(src, `${f}: allowlisted ${call} no longer exists — drop the entry`).toContain(`api.${call}(`)
      expect(why.length, `${f}/${call} needs a stated reason`).toBeGreaterThan(10)
    }
    expect(ALLOWED_SWALLOWS.length, 'the allowlist should stay small; a growing one means the rule is wrong').toBeLessThan(8)
  })

  it('the two writes this widening caught now report', () => {
    const upd = readFileSync(join(SETTINGS, 'UpdatesPanel.tsx'), 'utf8')
    expect(upd, 'the optimistic toggles must report').toMatch(/\.catch\(reportSettingFailure\(/)
    expect(upd).toMatch(/notify\(`Couldn't \$\{what\}: \$\{msg\}`, 'error'\)/)
    expect(upd, 'and still confirm on success — the `.then` was correct').toMatch(/\.then\(markSaved\)/)
    expect(upd, 'and `markSaved` is what actually shows the toast').toContain('const markSaved = () => { setSaved(true)')
  })
})


describe('a failure report lands where the failure happened', () => {
  const P = 'PortabilityPanel.tsx'
  const portability = () => readFileSync(join(SETTINGS, P), 'utf8')

  function catchBodies(src: string): string[] {
    const out: string[] = []
    for (const m of src.matchAll(/catch\s*\((\w+)\)\s*\{/g)) {
      let depth = 1, i = m.index! + m[0].length
      for (; i < src.length && depth > 0; i++) {
        if (src[i] === '{') depth++
        else if (src[i] === '}') depth--
      }
      out.push(src.slice(m.index!, i))
    }
    return out
  }

  it('every catch on the import/export panel reaches the error channel', () => {
    const bodies = catchBodies(portability())
    expect(bodies.length, 'the panel must still have its three failure paths').toBeGreaterThanOrEqual(3)
    for (const b of bodies) {
      expect(b, `a catch that only sets state cannot be found from the control:\n${b}`).toMatch(/notify\(/)
      expect(b, 'and it must be the error tone, not a neutral aside').toMatch(/'error'\)/)
    }
  })

  it('no failure path can write to the panel’s durable result line', () => {
    for (const b of catchBodies(portability())) {
      expect(b, `a failure must not write the import's result line:\n${b}`).not.toMatch(/setImportResult\(/)
    }
    const src = portability()
    expect(src, 'the old shared name must not come back').not.toMatch(/\bsetMsg\(/)
    expect(src, 'and the line still renders the import result').toMatch(/\{importResult && </)
  })

  it('the export path reports its own action by name, and still toasts on success', () => {
    const src = portability()
    expect(src, 'naming the action is what makes a toast locatable').toMatch(/Couldn't export \$\{spec\.label\.toLowerCase\(\)\}/)
    expect(src, 'success was already right — it must stay').toMatch(/export downloaded`, 'success'\)/)
  })

  it('the sibling panel on the same endpoints is still the precedent this converged onto', () => {
    const dur = readFileSync(join(SETTINGS, 'DurabilityPanel.tsx'), 'utf8')
    expect((dur.match(/, 'error'\)/g) || []).length,
      'DurabilityPanel reports its failures through notify()').toBeGreaterThanOrEqual(5)
  })

  const TONE = /text-danger|text-warn|var\(--color-danger\)|var\(--color-warning\)|bg-danger|border-danger|role="alert"|LoadError|AlertTriangle|<Banner/
  const MESSAGE_SHAPED = /^(msg|err|error)$|Msg$|Err$|Error$/

  function mutedFailureStates(): string[] {
    const out: string[] = []
    for (const f of readdirSync(SETTINGS).filter((x) => /\.tsx$/.test(x) && !/\.test\./.test(x))) {
      const src = readFileSync(join(SETTINGS, f), 'utf8')
      const caught = new Set<string>()
      for (const body of catchBodies(src)) {
        if (/notify\(/.test(body)) continue
        for (const s of body.matchAll(/\bset([A-Z]\w*)\(/g)) caught.add(s[1][0].toLowerCase() + s[1].slice(1))
      }
      for (const v of caught) {
        if (!MESSAGE_SHAPED.test(v)) continue
        const sites = [...src.matchAll(new RegExp(`\\{${v}(?:\\s*&&|\\})`, 'g'))]
        if (!sites.length) continue
        const toned = sites.filter((s) => TONE.test(src.slice(Math.max(0, s.index! - 320), s.index! + 200)))
        if (!toned.length) out.push(`${f}:${v}`)
      }
    }
    return out
  }

  it('the panel this cycle fixed has left the family', () => {
    expect(mutedFailureStates(), `${P} must no longer hold a muted failure message`)
      .not.toContain(`${P}:importResult`)
    expect(mutedFailureStates().filter((s) => s.startsWith(P)), `nor any other state in ${P}`).toEqual([])
  })

  it('the remainder is a measured ceiling of 6 — classify, do not add', () => {
    const muted = mutedFailureStates()
    expect(muted.length, `a caught failure in the hint's voice:\n${muted.join('\n')}`).toBeLessThanOrEqual(6)
    expect(muted.length, 'the census must still see the family it is bounding').toBeGreaterThan(0)
  })
})
