import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { EvalsPanel } from './EvalsPanel'


const SETTINGS = join(process.cwd(), "src/features/settings")
const PY = join(__dirname, "../../../../../runtime/gideon")
const py = (rel: string) => readFileSync(join(PY, rel), 'utf8')
const web = (rel: string) => readFileSync(join(SETTINGS, rel), 'utf8')


interface AllowEntry { type: string; min?: number; max?: number }

function allowlist(): Record<string, AllowEntry> {
  const src = py('interfaces/dashboard/handlers/core.py')
  const out: Record<string, AllowEntry> = {}
  const re = /^\s*"evals\.([a-z_]+)":\s*\{([^}]*)\},?\s*$/gm
  for (const m of src.matchAll(re)) {
    const body = m[2]
    const num = (name: string) => {
      const hit = new RegExp(`"${name}":\\s*(-?[0-9.]+)`).exec(body)
      return hit ? Number(hit[1]) : undefined
    }
    out[m[1]] = { type: /"type":\s*"(\w+)"/.exec(body)?.[1] ?? '', min: num('min'), max: num('max') }
  }
  return out
}


interface MetaField { label: string; help: string; def: string }

function topLevelSplit(body: string): string[] {
  const parts: string[] = []
  let depth = 0, quote = '', cur = ''
  for (const ch of body) {
    if (quote) { cur += ch; if (ch === quote) quote = ''; continue }
    if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue }
    if ('([{'.includes(ch)) depth++
    if (')]}'.includes(ch)) depth--
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; continue }
    cur += ch
  }
  if (cur.trim()) parts.push(cur)
  return parts
}

const joinLiterals = (frag: string) =>
  [...frag.matchAll(/"([^"]*)"|'([^']*)'/g)].map((m) => m[1] ?? m[2]).join('')

function balanced(src: string, from: number): string {
  const open = src.indexOf('(', from)
  let depth = 0, quote = ''
  for (let i = open; i < src.length; i++) {
    const ch = src[i]
    if (quote) { if (ch === quote) quote = ''; continue }
    if (ch === '"' || ch === "'") { quote = ch; continue }
    if (ch === '(') depth++
    else if (ch === ')') { depth--; if (depth === 0) return src.slice(open + 1, i) }
  }
  throw new Error('unbalanced')
}

function evalsMeta(): Record<string, MetaField> {
  const src = py('core/config/learning.py')
  const at = src.indexOf('class EvalsConfig:')
  expect(at, 'EvalsConfig must exist in config/learning.py').toBeGreaterThan(-1)
  const end = src.indexOf('\n@dataclass', at)
  const block = src.slice(at, end > -1 ? end : undefined)
  const out: Record<string, MetaField> = {}
  const re = /^    ([a-z_]+):\s*\w+\s*=\s*field\(/gm
  for (const m of block.matchAll(re)) {
    const body = balanced(block, m.index! + m[0].length - 1)
    const def = /default=([^,\n]+)/.exec(body)?.[1]?.trim() ?? ''
    const metaAt = body.indexOf('_meta(')
    const args = topLevelSplit(balanced(body, metaAt + 5))
    out[m[1]] = { label: joinLiterals(args[0] ?? ''), help: joinLiterals(args[1] ?? ''), def }
  }
  return out
}

const ALLOW = allowlist()
const META = evalsMeta()
const EDITABLE = Object.keys(ALLOW).sort()


describe('the derivation reads the real files', () => {
  it('finds the five allowlisted evals keys, and only those', () => {
    expect(py('interfaces/dashboard/handlers/core.py').length, 'the handler must be readable').toBeGreaterThan(5000)
    expect(EDITABLE).toEqual([
      'ablation_cadence_days', 'default_budget_usd', 'enabled',
      'judge_agreement_floor', 'study_default_k',
    ])
  })

  it('finds every EvalsConfig field with a non-empty label and help', () => {
    expect(py('core/config/learning.py').length).toBeGreaterThan(15000)
    expect(Object.keys(META).sort()).toEqual([...EDITABLE, 'bakeoff_capture_enabled'].sort())
    for (const [k, m] of Object.entries(META)) {
      expect(m.label.length, `${k} label`).toBeGreaterThan(3)
      expect(m.help.length, `${k} help`).toBeGreaterThan(60)
      expect(m.def, `${k} default`).not.toBe('')
    }
    expect(META.enabled.help).toContain('~/.gideon/evals/')
    expect(META.bakeoff_capture_enabled.help).toContain('privacy-sensitive')
  })

  it('the capture flag is IN the dataclass and OUT of the allowlist', () => {
    expect(META.bakeoff_capture_enabled, 'the field must still exist').toBeTruthy()
    expect(ALLOW.bakeoff_capture_enabled, 'and must NOT be one-click PATCHable').toBeUndefined()
    expect(py('interfaces/dashboard/handlers/core.py'), 'the exclusion must stay stated, not incidental')
      .toContain('`evals.bakeoff_capture_enabled`')
  })
})


const gideonConfig = vi.fn()
const patchConfig = vi.fn()
vi.mock('../../shared/data/api', () => ({
  api: {
    gideonConfig: (...a: unknown[]) => gideonConfig(...a),
    patchConfig: (...a: unknown[]) => patchConfig(...a),
  },
}))
const notify = vi.fn()
vi.mock('../../app/shell/appSdk', () => ({ notify: (...a: unknown[]) => notify(...a) }))

function savedConfig(): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, m] of Object.entries(META)) {
    out[k] = m.def === 'False' ? false : m.def === 'True' ? true : Number(m.def)
  }
  return out
}

const DEFAULTS = savedConfig()

async function mount() {
  gideonConfig.mockResolvedValue({ evals: { ...DEFAULTS } })
  const r = render(<EvalsPanel />)
  await waitFor(() => expect(screen.getByRole('switch', { name: META.enabled.label })).toBeTruthy())
  return r
}

const describedText = (el: Element) =>
  (el.getAttribute('aria-describedby') ?? '').split(/\s+/).filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? '').join(' ')

function hintEl(key: string): HTMLElement {
  const help = META[key].help.trim()
  const hit = [...document.querySelectorAll('div, p')]
    .find((el) => el.children.length === 0 && (el.textContent ?? '').trim().startsWith(help))
  expect(hit, `${key}: the rendered hint must BE the _meta help, not a second wording of it`).toBeTruthy()
  return hit as HTMLElement
}

describe('#/settings/evals surfaces exactly what the allowlist permits', () => {
  beforeEach(() => {
    sessionStorage.clear()
    localStorage.clear()
    vi.clearAllMocks()
    patchConfig.mockResolvedValue({})
  })

  it('renders one control per allowlisted key — and no others', async () => {
    const { container } = await mount()
    const controls = [...container.querySelectorAll('[role="switch"], input[type="number"]')]
    expect(controls.length, 'one control per allowlisted key, no more').toBe(EDITABLE.length)
  })

  it('names each control with the field’s own _meta label', async () => {
    await mount()
    for (const key of EDITABLE) {
      const label = META[key].label
      const role = ALLOW[key].type === 'bool' ? 'switch' : 'spinbutton'
      expect(screen.getByRole(role, { name: label }), `${key} must be named "${label}"`).toBeTruthy()
    }
  })

  it('describes each control with the field’s own _meta help', async () => {
    await mount()
    for (const key of EDITABLE) {
      const role = ALLOW[key].type === 'bool' ? 'switch' : 'spinbutton'
      const control = screen.getByRole(role, { name: META[key].label })
      const hint = hintEl(key)
      const row = hint.parentElement?.parentElement
      expect(row?.contains(control), `${key}: the hint must sit with its control`).toBe(true)
    }
  })

  it('and ASSOCIATES it, wherever the primitive supports one', async () => {
    await mount()
    for (const key of EDITABLE) {
      if (ALLOW[key].type === 'bool') continue
      const el = screen.getByRole('spinbutton', { name: META[key].label })
      expect(el.getAttribute('aria-describedby'), `${key} must be described`).toBe(hintEl(key).id)
      expect(describedText(el).trim().startsWith(META[key].help.trim()), `${key} description`).toBe(true)
    }
  })

  it('bounds every stepper by the allowlist’s own min/max', async () => {
    await mount()
    for (const key of EDITABLE) {
      if (ALLOW[key].type === 'bool') continue
      const el = screen.getByRole('spinbutton', { name: META[key].label })
      expect(Number(el.getAttribute('min')), `${key} min`).toBe(ALLOW[key].min)
      expect(Number(el.getAttribute('max')), `${key} max`).toBe(ALLOW[key].max)
    }
  })

  it('steps finely enough to express the value it is displaying', async () => {
    await mount()
    for (const key of EDITABLE) {
      if (ALLOW[key].type === 'bool') continue
      const el = screen.getByRole('spinbutton', { name: META[key].label })
      const step = Number(el.getAttribute('step'))
      const def = Number(META[key].def)
      const rungs = (def - (ALLOW[key].min ?? 0)) / step
      expect(Math.abs(rungs - Math.round(rungs)) < 1e-9,
        `${key}: step ${step} cannot reach its own default ${def}`).toBe(true)
    }
  })

  it('offers NO control for the privacy-gated capture flag', async () => {
    const { container } = await mount()
    expect(screen.queryByRole('switch', { name: META.bakeoff_capture_enabled.label })).toBeNull()
    expect(container.textContent).not.toMatch(/bake-?off/i)
    expect(web('EvalsPanel.tsx')).not.toContain('bakeoff_capture_enabled"')
  })

  it('PATCHes the allowlisted path, and rolls the row back when the save is refused', async () => {
    await mount()
    const sw = screen.getByRole('switch', { name: META.enabled.label })
    expect(sw.getAttribute('aria-checked')).toBe('false')
    fireEvent.click(sw)
    await waitFor(() => expect(patchConfig).toHaveBeenCalledWith('evals.enabled', true))
    await waitFor(() => expect(sw.getAttribute('aria-checked')).toBe('true'))

    patchConfig.mockRejectedValueOnce(new Error('nope'))
    fireEvent.click(sw)
    await waitFor(() => expect(sw.getAttribute('aria-checked')).toBe('true'))
    expect(notify.mock.calls.at(-1)?.[0]).toContain(META.enabled.label)
    expect(notify.mock.calls.at(-1)?.[0]).not.toContain('evals.enabled')
  })

  it('every numeric row patches its own allowlisted path', async () => {
    await mount()
    for (const key of EDITABLE) {
      if (ALLOW[key].type === 'bool') continue
      const el = screen.getByRole('spinbutton', { name: META[key].label })
      const next = (ALLOW[key].min ?? 0) + Number(el.getAttribute('step'))
      fireEvent.change(el, { target: { value: String(next) } })
      fireEvent.blur(el)
      await waitFor(() => expect(patchConfig).toHaveBeenCalledWith(`evals.${key}`, next))
    }
  })
})

describe('the switch says what turning it on costs', () => {
  beforeEach(() => { sessionStorage.clear(); vi.clearAllMocks(); patchConfig.mockResolvedValue({}) })

  it('names the model AND judge calls, before the first study runs', async () => {
    await mount()
    const hint = (hintEl('enabled').textContent ?? '').toLowerCase()
    expect(hint, 'the hint must say it spends model calls').toMatch(/model call/)
    expect(hint, 'and that the judge is one of them').toMatch(/judge/)
    expect(hint, 'and point at the budget knob below').toMatch(/budget/)
  })
})


describe('a user can find it', () => {
  it('is a registered subpage at #/settings/evals', () => {
    const page = web('SettingsPage.tsx')
    expect(page).toMatch(/\{ id: 'evals', label: 'Evaluations', icon: \w+, render: \(\) => <EvalsPanel \/> \}/)
  })

  it('has a card on the settings hub, which is the ONLY navigation', () => {
    const home = web('SettingsHome.tsx')
    expect(home).toContain('SETTINGS_WIDGETS')
    const widgets = web('settingsWidgets.tsx')
    expect(widgets).toMatch(/id: 'evals', group: '[^']+', label: 'Evaluations'/)
    const at = widgets.indexOf("id: 'evals'")
    const body = widgets.slice(at, at + 2600)
    expect(body).toMatch(/evals evaluations/)
    expect(body).toMatch(/loading=\{e === undefined && !evalErr\}/)
    expect(body).toMatch(/Boolean\(evalErr\) && <div/)
  })

  it('distinguishes both in-prose links by more than hue', () => {
    for (const [file, dir] of [['EvalsPanel.tsx', 'settings'], ['EvalsOff.tsx', 'learning']] as const) {
      const src = readFileSync(join(process.cwd(), "src/features", dir, file), 'utf8')
      const jsx = src.slice(src.indexOf('export function'))
      const link = /<TextLink[^>]*>/.exec(jsx)?.[0] ?? ''
      expect(link, `${file} must have an in-prose link`).toContain('href=')
      expect(link, `${file}: hue alone is not a distinction`).toContain('underline')
      expect(link, `${file}: the base accent fails AA on canvas`).toContain('ink="emphasis"')
    }
  })

  it('is where #/learning sends you when the substrate is off', () => {
    const off = readFileSync(join(process.cwd(), "src/features/learning/EvalsOff.tsx"), 'utf8')
    const jsx = off.slice(off.indexOf('export function EvalsOff'))
    expect(jsx, 'the link must be DEEP — the bare hub is 34 cards').toContain('href="#/settings/evals"')
    expect(jsx).toContain(META.enabled.label)
    expect(jsx, 'a dotted path is a terminal instruction, not a link').not.toMatch(/evals\.enabled/)
    expect(jsx, 'and the CLI command it replaced is gone — one instruction').not.toMatch(/config set/)
  })
})
