import { describe, expect, it, afterEach, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { api, ApiError, hasApiCode } from '../../shared/data/api'
import { errEnvelope, errText } from '../../shared/data/errText'
import { JudgeBenchPanel } from './JudgeBenchPanel'
import { StudiesPanel } from './StudiesPanel'
import { RetrievalBenchPanel } from './RetrievalBenchPanel'
import { AblationPanel } from './AblationPanel'
import { BenchmarkPanel } from './BenchmarkPanel'
import { FieldMetricsPanel } from './FieldMetricsPanel'


const WIRE = {
  judge_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish benchmark results."}}',
  studies_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish study results."}}',
  retrieval_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish retrieval ablation reports."}}',
  ablation_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish ablation reports."}}',
  benchmark_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish benchmark reports."}}',
  field_metrics_disabled: '{"error": {"code": "evals_disabled", "message": "The eval substrate is off. Turn on `evals.enabled` to publish field metrics."}}',
  judge_absent: '{"error": {"code": "judge_bench_absent", "message": "No judge benchmark has run yet. Run `gideon judge-bench` to produce one."}}',
  retrieval_absent: '{"error": {"code": "retrieval_absent", "message": "No retrieval benchmark has run yet. Run `gideon retrieval-eval` to score both stores."}}',
  ablation_absent: '{"error": {"code": "ablation_absent", "message": "No ablation has run yet. Register a component in `evals/ablation_registry.json` and run `gideon ablation --force`."}}',
} as const

const res = (body: string, status = 404) =>
  new Response(body, { status, headers: { 'Content-Type': 'application/json' } })

describe('errEnvelope keeps BOTH halves of the platform envelope', () => {
  it('lifts the code off every real /api/evals body', async () => {
    for (const [name, body] of Object.entries(WIRE)) {
      const { code, message } = await errEnvelope(res(body))
      expect(code, `${name} must carry a code`).toMatch(/^[a-z][a-z0-9_]*$/)
      expect(message.includes(code), `${name}: prose must not contain its own code`).toBe(false)
    }
  })

  it('leaves the human sentence byte-identical to errText', async () => {
    for (const body of Object.values(WIRE)) {
      const { message } = await errEnvelope(res(body))
      expect(message).toBe(await errText(res(body)))
    }
    for (const [body, want] of [
      ['{"error": "name is required"}', 'name is required'],
      ['{"detail": "theme store is read-only"}', 'theme store is read-only'],
      ['{"error": {"code": "x"}}', 'HTTP 404'],
      ['{"error": {"code": 7}}', 'HTTP 404'],
      ['', 'HTTP 404'],
    ] as const) {
      expect(await errEnvelope(res(body)).then((e) => e.message)).toBe(want)
      expect(await errText(res(body))).toBe(want)
    }
  })

  it('still lifts a code even when the message is unusable', async () => {
    const { code, message } = await errEnvelope(res('{"error": {"code": "evals_disabled"}}'))
    expect(code).toBe('evals_disabled')
    expect(message).toBe('HTTP 404')
  })

  it('reports no code for the 239 bare-string sites', async () => {
    expect((await errEnvelope(res('{"error": "disk full"}'))).code).toBe('')
    expect((await errEnvelope(res('nginx is down', 502))).code).toBe('')
  })
})

describe('the api client hands the code to its callers', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('populates ApiError.code from the envelope, through the real request helper', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res(WIRE.judge_disabled)))
    const err = await api.judgeBench().then(() => null, (e: unknown) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).code).toBe('evals_disabled')
    expect((err as ApiError).status).toBe(404)
    expect((err as ApiError).message)
      .toBe('The eval substrate is off. Turn on `evals.enabled` to publish benchmark results.')
  })

  it('populates it on the DELETE helper too, which throws its own ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res('{"error": {"code": "pack_not_installed", "message": "No such installed pack."}}')))
    const err = await api.deleteSnippet('x').then(() => null, (e: unknown) => e)
    expect(hasApiCode(err, 'pack_not_installed')).toBe(true)
  })

  it('leaves .code empty rather than guessing when the body carries none', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => res('{"error": "the gateway sentence"}', 500)))
    const err = await api.judgeBench().then(() => null, (e: unknown) => e)
    expect((err as ApiError).code).toBe('')
    expect(hasApiCode(err, 'evals_disabled')).toBe(false)
  })

  it('hasApiCode refuses a bare Error and a look-alike message', () => {
    expect(hasApiCode(new Error('evals_disabled'), 'evals_disabled')).toBe(false)
    expect(hasApiCode(new ApiError('evals_disabled', 404), 'evals_disabled')).toBe(false)
    expect(hasApiCode(new ApiError('off', 404, 'evals_disabled'), 'evals_disabled')).toBe(true)
    expect(hasApiCode(undefined, 'evals_disabled')).toBe(false)
  })
})

async function wireError(body: string): Promise<ApiError> {
  const { message, code } = await errEnvelope(res(body))
  return new ApiError(message, 404, code)
}

describe('every eval panel says "the substrate is off" — and says it alike', () => {
  const CASES = [
    { name: 'judge tiers', body: WIRE.judge_disabled, what: 'judge benchmark', el: (e: unknown) => <JudgeBenchPanel bench={undefined} error={e} onRetry={() => {}} /> },
    { name: 'template studies', body: WIRE.studies_disabled, what: 'study', el: (e: unknown) => <StudiesPanel studies={undefined} error={e} onRetry={() => {}} /> },
    { name: 'retrieval', body: WIRE.retrieval_disabled, what: 'retrieval benchmark', el: (e: unknown) => <RetrievalBenchPanel bench={undefined} error={e} onRetry={() => {}} /> },
    { name: 'ablation', body: WIRE.ablation_disabled, what: 'ablation', el: (e: unknown) => <AblationPanel view={undefined} error={e} onRetry={() => {}} /> },
    { name: 'skill-impact benchmark', body: WIRE.benchmark_disabled, what: 'benchmark', el: (e: unknown) => <BenchmarkPanel view={undefined} error={e} onRetry={() => {}} /> },
    { name: 'lab vs field', body: WIRE.field_metrics_disabled, what: 'lab-vs-field table', el: (e: unknown) => <FieldMetricsPanel rows={undefined} error={e} onRetry={() => {}} /> },
  ] as const

  for (const c of CASES) {
    it(`${c.name}: guidance, one instruction, and no Retry`, async () => {
      render(c.el(await wireError(c.body)))
      expect(screen.getByText(new RegExp(`no ${c.what} can run`))).toBeTruthy()
      expect(screen.getByRole('link', { name: 'Evals enabled in Settings → Evaluations' })).toBeTruthy()
      expect(screen.queryByText(/gideon config set/), 'two ways to flip one switch is not guidance').toBeNull()
      expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
      expect(screen.queryByRole('alert')).toBeNull()
      expect(screen.queryByText(/Couldn't load your/)).toBeNull()
    })
  }

  it('and the list above is the WHOLE population — every panel handling evals_disabled renders EvalsOff', () => {
    const HERE = import.meta.dirname
    const files = readdirSync(HERE).filter((n) => /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n))
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const handlers: string[] = []
    const offenders: string[] = []
    for (const n of files) {
      if (n === 'EvalsOff.tsx') continue
      const src = strip(readFileSync(join(HERE, n), 'utf8'))
      if (!/hasApiCode\(error, 'evals_disabled'\)/.test(src)) continue
      handlers.push(n)
      if (!/<EvalsOff\b/.test(src)) offenders.push(`${n}: branches on evals_disabled without rendering <EvalsOff>`)
      if (/<code[^>]*>\s*evals\.enabled/.test(src)) offenders.push(`${n}: prints the dotted config path`)
      if (/href="#\/settings"/.test(src)) offenders.push(`${n}: links the 34-card hub, not #/settings/evals`)
    }
    expect(handlers.length, 'no panel branches on evals_disabled — the scan is wrong').toBeGreaterThanOrEqual(5)
    expect(handlers.length, 'a panel handles evals_disabled but is not in CASES above').toBe(CASES.length)
    expect(
      offenders,
      'EvalsOff owns this sentence — its docstring records why the dotted path and the hub link are ' +
        'wrong:\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('sends the user to the SUBPAGE that has the control, never the bare hub', async () => {
    for (const c of CASES) {
      const { unmount } = render(c.el(await wireError(c.body)))
      const link = document.querySelector('a[href="#/settings/evals"]')
      expect(link, `${c.name} must link the evals subpage`).not.toBeNull()
      expect(document.querySelector('a[href="#/settings"]'), 'the bare hub is the dead end').toBeNull()
      expect(document.querySelectorAll('a').length, `${c.name}: one instruction`).toBe(1)
      unmount()
    }
  })

  it('keeps its LABELLED section, so the panel does not vanish from the page', async () => {
    for (const [c, id] of [
      [CASES[0], 'judge-bench-heading'], [CASES[1], 'studies-heading'],
      [CASES[2], 'retrieval-bench-heading'], [CASES[3], 'ablation-heading'],
    ] as const) {
      const { unmount } = render(c.el(await wireError(c.body)))
      expect(document.querySelector(`section[aria-labelledby="${id}"]`), `${c.name} keeps its section`).not.toBeNull()
      expect(document.getElementById(id)?.textContent?.trim(), `${c.name} keeps its name`).toBeTruthy()
      unmount()
    }
  })
})

describe('and the "nothing has run yet" branches, which were inert for the same reason', () => {
  it('judge tiers names its run command', async () => {
    render(<JudgeBenchPanel bench={undefined} error={await wireError(WIRE.judge_absent)} onRetry={() => {}} />)
    expect(screen.getByText('gideon judge-bench')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('retrieval names its run command AND still offers the label card', async () => {
    render(<RetrievalBenchPanel bench={undefined} error={await wireError(WIRE.retrieval_absent)} onRetry={() => {}} />)
    expect(screen.getByText('gideon retrieval-eval')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('ablation points at the registry — the place the OFF state must not mention', async () => {
    render(<AblationPanel view={undefined} error={await wireError(WIRE.ablation_absent)} onRetry={() => {}} />)
    expect(screen.getByText('evals/ablation_registry.json')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('the OFF state does not leak any of those three next steps', async () => {
    const { unmount } = render(<AblationPanel view={undefined} error={await wireError(WIRE.ablation_disabled)} onRetry={() => {}} />)
    expect(screen.queryByText(/ablation_registry/)).toBeNull()
    unmount()
    render(<JudgeBenchPanel bench={undefined} error={await wireError(WIRE.judge_disabled)} onRetry={() => {}} />)
    expect(screen.queryByText(/gideon judge-bench/)).toBeNull()
  })
})

describe('a real failure is still a failure', () => {
  const GENERIC = new ApiError('The benchmark artifacts could not be read.', 500, 'judge_bench_unreadable')

  it('an unrecognised code renders LoadError, with the Retry that belongs there', () => {
    render(<JudgeBenchPanel bench={undefined} error={GENERIC} onRetry={() => {}} />)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
    expect(screen.getByText(/Couldn't load your judge benchmark/)).toBeTruthy()
    expect(screen.queryByText(/gideon judge-bench/)).toBeNull()
    expect(screen.queryByText(/substrate is off/)).toBeNull()
  })

  it('a codeless network blip does too', () => {
    render(<AblationPanel view={undefined} error={new Error('Failed to fetch')} onRetry={() => {}} />)
    expect(screen.getByRole('alert')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy()
  })

  it('an EMPTY study register is a 200, not this branch', () => {
    render(<StudiesPanel studies={[]} error={undefined} onRetry={() => {}} />)
    expect(screen.getByText(/No study has been registered/)).toBeTruthy()
    expect(screen.queryByText(/substrate is off/)).toBeNull()
  })
})

describe('one owner for the predicate', () => {
  it('no panel re-derives a code from prose', async () => {
    const { readFileSync, readdirSync } = await import('node:fs')
    const { join } = await import('node:path')
    const dir = join(process.cwd(), "src/features/learning")
    const offenders = readdirSync(dir)
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
      .filter((f) => /\.message\b[^\n]*\.includes\(|includes\(['"](?:evals_disabled|[a-z_]+_absent)['"]\)/
        .test(readFileSync(join(dir, f), 'utf8')))
    expect(offenders, 'match on ApiError.code via hasApiCode, never on the message').toEqual([])
  })
})
