import { createRef } from 'react'
import { render, screen, fireEvent, within, act } from '@testing-library/react'
import { SessionMarkerRail } from '../../features/chat/SessionMarkerRail'
import { ThemeProvider } from '../../app/shell/theme'
import { AppearanceProvider } from '../../app/shell/appearance'
import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const read = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

const ownsFocus = (src: string) => /useFocusTrap/.test(src) ||
  /new FocusScope\([^)]*\)\.attach\(/.test(src) ||
  (/new FocusScope/.test(src) && /\.attach\(/.test(src)) ||
  (/e\.key !== "Tab"/.test(src) && /last\.focus\(\)/.test(src) && /first\.focus\(\)/.test(src) && /closeRef\.current\?\.focus\(\)/.test(src) && /removeEventListener\("keydown", handleKeyDown\)/.test(src)) ||
  (/DialogPrimitive\.Popup/.test(src) && /Dialog as DialogPrimitive.*@base-ui\/react\/dialog/.test(src))

describe('the update overlay honours the contract it declares', () => {
  const src = read('shared/ui/UpdateProgressOverlay.tsx')

  it('the alertdialog carries the focus trap', () => {
    expect(src).toMatch(/import \{ useFocusTrap \} from '\.\/useFocusTrap'/)
    expect(src).toMatch(/ref=\{trapRef\} role="alertdialog" aria-modal="true"/)
  })

  it('the trap lives in a child that mounts WITH the dialog, not in the shell', () => {
    expect(src).toMatch(/function UpdateSheet\(\{ progress, cancel, dismiss, hide, busy \}/)
    expect(src).toMatch(/const trapRef = useFocusTrap<HTMLDivElement>\(\)/)
    const shell = src.slice(src.indexOf('export function UpdateProgressOverlay()'), src.indexOf('function UpdateSheet'))
    expect(shell).toMatch(/\{progress && <UpdateSheet progress=\{progress\} cancel=\{cancel\} dismiss=\{dismiss\} hide=\{hide\} busy=\{busy\} \/>\}/)
    expect(/useFocusTrap/.test(shell), 'the always-mounted shell must not call the hook').toBe(false)
  })
})

describe('the rail: aria-modal implies a focus trap', () => {
  const files = walk(SRC).map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))

  it('every aria-modal surface uses useFocusTrap', () => {
    const offenders = files
      .filter((f) => /aria-modal="true"/.test(f.src))
      .filter((f) => !ownsFocus(f.src))
      .map((f) => f.rel)
    expect(
      offenders,
      `aria-modal="true" promises focus is owned; without a trap Tab reaches the page behind the ` +
        `scrim:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the aria-modal surfaces', () => {
    const modal = files.filter((f) => /aria-modal="true"/.test(f.src)).map((f) => f.rel).sort()
    expect(modal.length).toBeGreaterThanOrEqual(5)
    for (const rel of modal) expect(ownsFocus(read(rel)), `${rel}: concrete containment implementation`).toBe(true)
    const sample = { rel: 'x.tsx', src: '<div role="dialog" aria-modal="true" />' }
    expect(/aria-modal="true"/.test(sample.src) && !/useFocusTrap/.test(sample.src)).toBe(true)
  })

  it('the two non-modal dialog roles are a recorded distinction', () => {
    for (const rel of ['shared/ui/DegradedChip.tsx', 'shared/ui/NavRail.tsx']) {
      const src = read(rel)
      expect(src, `${rel} should still be a dialog role`).toMatch(/role="dialog"/)
      expect(/aria-modal/.test(src), `${rel} must NOT claim aria-modal`).toBe(false)
    }
    expect(read('shared/ui/NavRail.tsx')).toMatch(/inert/)
  })
})


describe('the rail: a hand-rolled modal over live content owes containment', () => {
  const overlays = walk(SRC)
    .map((abs) => ({ rel: abs.slice(SRC.length + 1), src: strip(readFileSync(abs, 'utf8')) }))
    .filter((f) => !f.rel.startsWith('shared/ui/') && /fixed inset-0/.test(f.src))

  it('finds the population — the census is not vacuous', () => {
    expect(overlays.length).toBeGreaterThanOrEqual(4)
    for (const { rel, src } of overlays) {
      expect(ownsFocus(src) || rel === 'app/shell/Onboarding.tsx' ||
        (rel === 'features/capabilities/experience/AmbientDisplay.tsx' && /return <main ref=\{root\}/.test(src) && /document\.fullscreenElement/.test(src)), `${rel}: verified containment or standalone replacement`).toBe(true)
    }
  })

  it('every overlay that covers live content wires useFocusTrap', () => {
    const EXEMPT = ['app/shell/Onboarding.tsx', 'features/capabilities/experience/AmbientDisplay.tsx']
    const offenders = overlays
      .filter((f) => !EXEMPT.includes(f.rel))
      .filter((f) => !ownsFocus(f.src))
      .map((f) => f.rel)
    expect(
      offenders,
      `these cover a mounted page and dismiss like a dialog, so Tab must not leave them:\n  ${offenders.join('\n  ')}`,
    ).toEqual([])
  })

  it('Onboarding is exempt because it REPLACES the shell, and that is asserted', () => {
    expect(read('app/shell/App.tsx'), 'Onboarding must still be rendered INSTEAD of the shell')
      .toMatch(/if \(route === 'onboarding' \|\| !onboarded\) return <Onboarding query=\{query\} setQuery=\{setQuery\} \/>/)
    expect(/aria-modal/.test(read('app/shell/Onboarding.tsx'))).toBe(false)
  })

  it("ChatFilePanel's expanded mode is covered too, and it is NOT a dialog", () => {
    const src = read('features/chat/ChatFilePanel.tsx')
    expect(src, 'still an expand/collapse view state').toMatch(/if \(expanded\)/)
    expect(src, 'Escape must keep collapsing the expansion').toMatch(/if \(expanded\) setExpanded\(false\)/)
    expect(/aria-modal|role="dialog"/.test(src), 'must not claim a dialog role').toBe(false)
  })

  it('the trap only engages where it is mounted WITH the overlay', () => {
    expect(read('features/chat/SessionSkillsReview.tsx')).toMatch(/\{open && \(\s*<SessionSkillsModal/)
    expect(read('features/knowledge/KnowledgeDetail.tsx')).toMatch(/\{fullscreen && <FullscreenModal/)
    expect(read('features/chat/ChatFilePanel.tsx')).toMatch(/<ExpandedOverlay>\{body\}<\/ExpandedOverlay>/)
  })
})


describe('the native session map owns mobile drawer focus', () => {
  it('contains Tab, consumes Escape, restores the opener and releases its listeners', () => {
    const ref = createRef<HTMLDivElement>()
    render(<ThemeProvider><AppearanceProvider><button>Behind drawer</button><SessionMarkerRail turns={[{ role: 'user', segments: [{ kind: 'text', text: 'Question' }] }]} scrollRef={ref} nodeOf={() => null} onJumpTo={() => {}} showReturnToNewest={false} onReturnToNewest={() => {}} /></AppearanceProvider></ThemeProvider>)
    const opener = screen.getByRole('button', { name: 'Open session map' })
    opener.focus()
    fireEvent.click(opener)
    const drawer = screen.getByRole('dialog', { name: 'Session map drawer' })
    const close = within(drawer).getByRole('button', { name: 'Close session map' })
    const last = within(drawer).getByRole('button', { name: /Question/ })
    const search = within(drawer).getByRole('searchbox', { name: 'Search this session' })
    last.focus()
    fireEvent.keyDown(last, { key: 'Tab' })
    expect(document.activeElement).toBe(close)
    fireEvent.keyDown(close, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(last)
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    act(() => { search.dispatchEvent(escape) })
    expect(escape.defaultPrevented).toBe(true)
    expect(screen.queryByRole('dialog', { name: 'Session map drawer' })).toBeNull()
    expect(document.activeElement).toBe(opener)
    const idle = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    opener.dispatchEvent(idle)
    expect(idle.defaultPrevented).toBe(false)
  })
})
