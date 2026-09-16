import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { unavailableWhen } from './unavailable'


describe('unavailableWhen', () => {
  it('makes the control reachable-but-unavailable, with the reason on title', () => {
    const props = unavailableWhen(true, 'Enter a host first')
    expect(props['aria-disabled']).toBe(true)
    expect(props.disabled, 'the native attribute would remove the tab stop').toBeUndefined()
    expect(props.title).toBe('Enter a host first')
  })

  it('returns nothing when the input is present', () => {
    expect(unavailableWhen(false, 'Enter a host first')).toEqual({})
  })

  it('preserves an existing title, appending the reason', () => {
    const props = unavailableWhen(true, 'Enter a host first', { title: 'Add to the denylist' })
    expect(props.title).toBe('Add to the denylist — Enter a host first')
  })

  it('keeps a plain title when nothing is missing', () => {
    expect(unavailableWhen(false, 'x', { title: 'Add' })).toEqual({ title: 'Add' })
  })

  it('goes natively disabled while busy AND announces itself as busy', () => {
    const props = unavailableWhen(true, 'Enter a host first', { busy: true })
    expect(props.disabled).toBe(true)
    expect(props['aria-busy'], 'busy must announce as working, not as unavailable').toBe(true)
    expect(props['aria-disabled']).toBeUndefined()
    expect(props.title, 'no reason is appended on the busy branch').toBeUndefined()
  })

  it('🪤 the NON-busy branches carry no aria-busy — it is not a blanket addition', () => {
    expect(unavailableWhen(true, 'Enter a host first')['aria-busy']).toBeUndefined()
    expect(unavailableWhen(false, 'x')['aria-busy']).toBeUndefined()
    expect(unavailableWhen(false, 'x', { title: 'Add' })['aria-busy']).toBeUndefined()
  })

  it('the fix REACHES the busy call sites, measured — not assumed from one unit test', () => {
    const SRC = join(__dirname, "../..")
    const strip = (s: string) => s
      .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
      .replace(/^(\s*)\/\/.*$/gm, '$1')
    const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
    })
    let busySites = 0
    for (const abs of walk(SRC)) {
      const code = strip(readFileSync(abs, 'utf8'))
      for (const m of code.matchAll(/\{\.\.\.unavailableWhen\([\s\S]{0,200}?\)\}/g)) {
        if (/busy/.test(m[0])) busySites += 1
      }
    }
    expect(busySites, 'busy-passing call sites, all of which now announce').toBeGreaterThanOrEqual(9)
  })

  it('busy wins even when nothing is missing', () => {
    expect(unavailableWhen(false, 'x', { busy: true }).disabled).toBe(true)
  })

  it('refuses the click on the CAPTURE phase', () => {
    const onClick = vi.fn()
    render(
      <button type="button" onClick={onClick} {...unavailableWhen(true, 'Enter a host first')}>
        Add
      </button>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).not.toHaveBeenCalled()
  })

  it('lets the click through once the input is present', () => {
    const onClick = vi.fn()
    render(
      <button type="button" onClick={onClick} {...unavailableWhen(false, 'x')}>
        Add
      </button>,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('keeps the control findable by its own name', () => {
    render(<button type="button" {...unavailableWhen(true, 'Enter a host first')}>Add</button>)
    expect(screen.getByRole('button', { name: 'Add' })).toBeTruthy()
  })
})

const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const ADOPTERS = [
  'app/shell/Onboarding.tsx',
  'shared/ui/PlanningWalkthrough.tsx',
  'features/ChatPage.tsx',
  'features/chat/ChatActivityPanel.tsx',
  'features/tasks/TaskDetail.tsx',
  'features/settings/SecurityPanel.tsx',
  'features/settings/VoicePanel.tsx',
  'features/settings/ProjectionRulesPanel.tsx',
  'features/settings/OllamaModelManager.tsx',
]

describe('every converted raw submit uses the helper', () => {
  for (const rel of ADOPTERS) {
    it(`${rel} spreads unavailableWhen on its gated submit`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must spread the helper').toMatch(/\{\.\.\.unavailableWhen\(/)
      expect(src, 'must import it').toMatch(/import \{[^}]*\bunavailableWhen\b[^}]*\} from '[^']*unavailable'/)
    })
  }

  it('every converted button dims at ITS OWN level, not a blanket one', () => {
    const mismatched: string[] = []
    const missing: string[] = []
    for (const rel of ADOPTERS) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      for (const m of src.matchAll(/unavailableWhen\(/g)) {
        const start = src.lastIndexOf('<', m.index!)
        let depth = 0
        let end = -1
        for (let i = start; i < src.length; i++) {
          const ch = src[i]
          if (ch === '{') depth++
          else if (ch === '}') depth--
          else if (ch === '>' && depth === 0) {
            end = i
            break
          }
        }
        const tag = src.slice(start, end + 1)
        const at = `${rel}:${src.slice(0, start).split('\n').length}`
        const aria = /aria-disabled:opacity-(\d+)/.exec(tag)
        const own = /(?<!aria-)disabled:opacity-(\d+)/.exec(tag)
        if (!aria) missing.push(at)
        else if (own && own[1] !== aria[1]) mismatched.push(`${at} (${own[1]} → ${aria[1]})`)
      }
    }
    expect(
      missing,
      'a converted button with no aria-disabled dimming looks enabled while refusing clicks:\n  ' +
        missing.join('\n  '),
    ).toEqual([])
    expect(
      mismatched,
      'the aria-disabled dim level must equal the button\'s own disabled level, or the conversion ' +
        'changes how it looks:\n  ' + mismatched.join('\n  '),
    ).toEqual([])
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})
