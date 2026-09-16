import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { Toggle } from './Toggle'


describe('a Toggle with a reason stays reachable', () => {
  const props = { on: false, label: 'Require a 2FA code', disabled: true }

  it('drops the native attribute so the tab stop survives', () => {
    render(<Toggle {...props} onChange={vi.fn()} disabledReason="Enroll an authenticator first" />)
    const el = screen.getByRole('switch') as HTMLButtonElement
    expect(el.disabled, 'a natively disabled switch leaves the tab order').toBe(false)
    expect(el.getAttribute('aria-disabled')).toBe('true')
  })

  it('announces the reason', () => {
    render(<Toggle {...props} onChange={vi.fn()} disabledReason="Enroll an authenticator first" />)
    expect(screen.getByRole('switch').getAttribute('title')).toBe('Enroll an authenticator first')
  })

  it('still refuses to toggle', () => {
    const onChange = vi.fn()
    render(<Toggle {...props} onChange={onChange} disabledReason="Enroll an authenticator first" />)
    screen.getByRole('switch').click()
    expect(onChange, 'aria-disabled without a suppressed handler is a lie').not.toHaveBeenCalled()
  })

  it('keeps its role, state and name while unavailable', () => {
    render(<Toggle on onChange={vi.fn()} label="Require a 2FA code" disabled disabledReason="x" />)
    const el = screen.getByRole('switch', { name: 'Require a 2FA code' })
    expect(el.getAttribute('aria-checked')).toBe('true')
  })

  it('carries BOTH dimming selectors, because one of them cannot match', () => {
    render(<Toggle {...props} onChange={vi.fn()} disabledReason="x" />)
    const cls = screen.getByRole('switch').className
    expect(cls).toMatch(/\baria-disabled:opacity-40\b/)
    expect(cls, 'the native selector still serves the ten sites that keep it').toMatch(/\bdisabled:opacity-40\b/)
  })

  it('changes nothing when no reason is given', () => {
    render(<Toggle {...props} onChange={vi.fn()} />)
    const el = screen.getByRole('switch') as HTMLButtonElement
    expect(el.disabled).toBe(true)
    expect(el.getAttribute('aria-disabled')).toBeNull()
    expect(el.getAttribute('title')).toBeNull()
  })
})

describe('the triage, pinned per site', () => {
  const SRC = join(process.cwd(), "src")
  const account = readFileSync(join(SRC, 'features/settings/AccountPanel.tsx'), 'utf8')

  it('all four precondition switches name what unlocks them', () => {
    const PRECONDITION: [string, RegExp][] = [
      ['features/settings/AccountPanel.tsx', /(?<!aria-)disabled=\{!state\.credential_configured\}/],
      ['features/settings/AccountPanel.tsx', /(?<!aria-)disabled=\{!state\.totp_enabled\}/],
      ['features/settings/MemoryPanel.tsx', /(?<!aria-)disabled=\{s\.graph_enabled === false\}/],
      ['features/settings/VoicePanel.tsx', /(?<!aria-)disabled=\{!bound\}/],
    ]
    for (const [rel, gate] of PRECONDITION) {
      const src = rel.endsWith('AccountPanel.tsx') ? account : readFileSync(join(SRC, rel), 'utf8')
      const tag = [...src.matchAll(/<Toggle\b[\s\S]{0,400}?\/>/g)].map((m) => m[0]).find((t) => gate.test(t))
      expect(tag, `${rel}: the switch gated by ${gate} must still exist`).toBeTruthy()
      expect(tag!, `${rel}: a precondition switch must say what unlocks it`).toMatch(/disabledReason="/)
    }
  })

  it('an in-flight toggle keeps the native attribute', () => {
    for (const rel of [
      'features/schedule/ScheduleDetail.tsx',
      'features/tools/ToolGroupsTile.tsx',
      'features/triggers/StoreTriggerDetail.tsx',
      'features/knowledge/SourcesPage.tsx',
      'features/knowledge/ReportsPage.tsx',
    ]) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const tag = src.match(/<Toggle\b[\s\S]{0,300}?\/>/)?.[0] ?? ''
      expect(tag, `${rel} must still gate on busy`).toMatch(/(?<!aria-)disabled=\{busy\}/)
      expect(tag, `${rel} must NOT soften an in-flight action`).not.toMatch(/disabledReason/)
    }
  })

  it('the census is reproducible — 16 disabled Toggle sites, not vacuously zero', () => {
    const walk = (d: string): string[] =>
      readFileSync !== undefined
        ? require('node:fs').readdirSync(d, { withFileTypes: true }).flatMap((e: { name: string; isDirectory(): boolean }) =>
          e.isDirectory() ? walk(join(d, e.name)) : (/\.tsx$/.test(e.name) && !/\.(test|doc)\.tsx$/.test(e.name) ? [join(d, e.name)] : []))
        : []
    const sites = walk(SRC).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(/<Toggle\b[\s\S]{0,400}?\/>/g)]
        .filter((m) => /(?<!aria-)disabled=/.test(m[0])))
    expect(sites.length).toBe(25)
    expect(sites.filter((m) => /disabledReason/.test(m[0])).length, 'eight carry a reason; seventeen stay native').toBe(8)
  })
})
