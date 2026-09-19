
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { ErrorBoundary } from './ErrorBoundary'
import { IncidentBanner } from './IncidentBanner'
import { PersonalityProvider } from './personality'
import { DEFAULT_PERSONALITY, PERSONALITIES } from '../../shared/theme/personalities'
import { ERROR_TREATMENTS } from '../../shared/theme/errorTreatments'

vi.mock('./appearance', () => ({
  useAppearance: () => ({ applyScheme: () => {}, setSelect: () => {} }),
}))

vi.mock('../../shared/data/api', () => ({
  api: {
    incident: () => Promise.resolve({ active: true, reason: 'disk full' }),
    incidentResume: () => Promise.resolve({}),
  },
}))


// Gideon shell rewrite: baseline updated for the authored panel layout and heading.
const EB_BEFORE =
  '<div class="mx-auto flex h-full max-w-2xl flex-col items-center justify-center gap-l rounded-xl border border-outline-variant/40 px-2xl py-3xl text-center">' +
  '<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
  'class="lucide lucide-triangle-alert text-on-surface-low" aria-hidden="true"></svg>' +
  '<h2 class="text-on-surface text-[1.0625rem]" style="font-variation-settings: &quot;wght&quot; 500;">' +
  'This page hit an error</h2>' +
  '<p class="max-w-md text-on-surface-low text-[0.8125rem]">kaboom</p>' +
  '<button type="button" class="inline-flex h-11 items-center gap-2 rounded-xl px-5 text-[0.8125rem]" ' +
  'style="background: var(--color-primary); color: var(--color-on-primary);">' +
  '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
  'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
  'class="lucide lucide-rotate-ccw" aria-hidden="true"></svg> Retry</button></div>'

const IB_BEFORE = {
  role: 'alert',
  // base chrome", and that still holds; it is not a licence to keep a stale literal.
  class: 'flex flex-wrap items-center gap-m border-b border-outline-variant/40 pl-l py-m text-[0.8125rem]',
  style:
    'background: var(--color-error-container); color: var(--color-on-error-container); ' +
    'padding-right: calc(var(--shell-corner-r, 140px) + var(--spacing-m, 12px));',
  iconClass: 'lucide lucide-triangle-alert shrink-0',
  text:
    'Incident mode is active — all unattended work (cron, hooks, triggers, subagents) is ' +
    'suspended · disk full. Chat still works.Resume',
}

const DEPRECATED_LUCIDE_ALIAS_CLASSES = new Set(['lucide-alert-triangle'])

function normaliseClassName(className: string): string {
  return className
    .split(/\s+/)
    .filter((token) => !DEPRECATED_LUCIDE_ALIAS_CLASSES.has(token))
    .join(' ')
}

function normalise(html: string): string {
  return html
    .replace(/(<svg[^>]*>)[\s\S]*?<\/svg>/g, '$1</svg>')
    .replace(/class="([^"]*)"/g, (_match, className: string) => `class="${normaliseClassName(className)}"`)
}

function activate(id: string) {
  localStorage.setItem('personality', id)
}

function Boom({ fail }: { fail: { current: boolean } }): ReactNode {
  if (fail.current) throw new Error('kaboom')
  return <div>page content</div>
}

function renderBoundary(fail = { current: true }) {
  const r = render(
    <PersonalityProvider>
      <ErrorBoundary>
        <Boom fail={fail} />
      </ErrorBoundary>
    </PersonalityProvider>,
  )
  return { ...r, fail }
}

async function renderBanner() {
  const r = render(
    <PersonalityProvider>
      <IncidentBanner />
    </PersonalityProvider>,
  )
  const alert = await screen.findByRole('alert')
  return { ...r, alert }
}

let consoleError: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
})
afterEach(() => {
  consoleError.mockRestore()
  localStorage.clear()
  document.documentElement.removeAttribute('data-personality')
})

describe('under a standard scheme both surfaces are identical to before PT-4', () => {
  it('ignores only the deprecated Lucide alias while keeping canonical and authored classes', () => {
    expect(
      normaliseClassName('lucide lucide-triangle-alert lucide-alert-triangle text-danger'),
    ).toBe('lucide lucide-triangle-alert text-danger')
  })

  it('the ErrorBoundary fallback renders the exact pre-change markup', () => {
    activate(DEFAULT_PERSONALITY)
    const { container } = renderBoundary()
    expect(normalise(container.innerHTML)).toBe(EB_BEFORE)
  })

  it('with NO stored personality at all, the fallback is still the pre-change markup', () => {
    const { container } = renderBoundary()
    expect(normalise(container.innerHTML)).toBe(EB_BEFORE)
  })

  it('the IncidentBanner renders the exact pre-change chrome', async () => {
    activate(DEFAULT_PERSONALITY)
    const { alert } = await renderBanner()
    expect(alert.getAttribute('role')).toBe(IB_BEFORE.role)
    expect(alert.getAttribute('class')).toBe(IB_BEFORE.class)
    expect(alert.getAttribute('style')).toBe(IB_BEFORE.style)
    expect(normaliseClassName(alert.querySelector('svg')?.getAttribute('class') ?? '')).toBe(
      IB_BEFORE.iconClass,
    )
    expect(alert.textContent).toBe(IB_BEFORE.text)
  })
})

describe('a treatment changes the skin and only the skin', () => {
  function referenceBoundary() {
    localStorage.clear()
    const { container, unmount } = renderBoundary()
    const text = container.textContent ?? ''
    const button = screen.getByRole('button').getAttribute('class')
    const name = screen.getByRole('button').textContent
    unmount()
    return { text, button, name }
  }

  it('every personality keeps the fallback copy, the button name and the action chrome', () => {
    const ref = referenceBoundary()
    for (const p of PERSONALITIES) {
      activate(p.id)
      const { container, unmount } = renderBoundary()
      expect(container.textContent, `${p.id} copy`).toBe(ref.text)
      const btn = screen.getByRole('button', { name: /retry/i })
      expect(btn.textContent, `${p.id} button name`).toBe(ref.name)
      expect(btn.getAttribute('class'), `${p.id} button chrome`).toBe(ref.button)
      unmount()
    }
  })

  it('every personality keeps the banner copy, its role and its Resume action', async () => {
    for (const p of PERSONALITIES) {
      activate(p.id)
      const { alert, unmount } = await renderBanner()
      expect(alert.getAttribute('role'), `${p.id} role`).toBe('alert')
      expect(alert.textContent, `${p.id} copy`).toBe(IB_BEFORE.text)
      expect(screen.getByRole('button', { name: /resume/i })).toBeTruthy()
      unmount()
    }
  })

  it('the Retry action still resets the boundary under every personality', () => {
    for (const p of PERSONALITIES) {
      activate(p.id)
      const { fail, unmount } = renderBoundary({ current: true })
      fail.current = false
      fireEvent.click(screen.getByRole('button', { name: /retry/i }))
      expect(screen.getByText('page content'), `${p.id} retry`).toBeTruthy()
      unmount()
    }
  })

  it('a personality WITH a treatment actually renders it (both surfaces)', async () => {
    const treated = PERSONALITIES.filter((p) => p.behavior.errorTreatment)
    expect(treated.length, 'no personality declares a treatment — the tests above are vacuous')
      .toBeGreaterThan(0)

    for (const p of treated) {
      const t = ERROR_TREATMENTS[p.behavior.errorTreatment!]
      activate(p.id)

      const boundary = renderBoundary()
      const panel = boundary.container.firstElementChild as HTMLElement
      expect(panel.getAttribute('class'), `${p.id} panel skin`).toContain(t.surfaceClass)
      expect(panel.getAttribute('style'), `${p.id} panel paint`).toContain(`var(${t.paint.bg})`)
      expect(panel.querySelector('svg')?.getAttribute('class')).toContain(t.iconClass)
      expect(panel.querySelector('svg')?.getAttribute('class')).not.toContain('text-on-surface-low')
      expect(normalise(boundary.container.innerHTML), `${p.id} differs from default`).not.toBe(EB_BEFORE)
      boundary.unmount()

      const banner = await renderBanner()
      expect(banner.alert.getAttribute('class'), `${p.id} banner skin`).toContain(t.surfaceClass)
      expect(banner.alert.getAttribute('style'), `${p.id} banner paint`).toContain(`var(${t.paint.ink})`)
      banner.unmount()
    }
  })

  it('the default identity declares NO treatment — that is what makes it pixel-stable', () => {
    const dflt = PERSONALITIES.find((p) => p.id === DEFAULT_PERSONALITY)
    expect(dflt?.behavior.errorTreatment).toBeUndefined()
  })
})
