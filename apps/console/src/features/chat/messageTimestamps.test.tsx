import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { AssistantActions, UserActions } from './MessageActions'
import { clockTime, fullStamp, isoStamp } from '../../shared/data/epoch'


const SRC = join(process.cwd(), "src")
const codeOf = (...p: string[]) =>
  readFileSync(join(SRC, ...p), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const NOOP = { onCopy: vi.fn(), onRegenerate: vi.fn(), onFork: vi.fn(), onSpeak: vi.fn() }
const TS = '2026-09-07T14:32:05.123456+00:00'

describe('the absolute formatters refuse to invent a time', () => {
  it('renders nothing for every unreadable stamp', () => {
    for (const bad of [undefined, null, '', 'not a date', NaN]) {
      expect(clockTime(bad as string), `clockTime(${String(bad)})`).toBe('')
      expect(fullStamp(bad as string), `fullStamp(${String(bad)})`).toBe('')
      expect(isoStamp(bad as string), `isoStamp(${String(bad)})`).toBe('')
    }
  })

  it('reads an ISO string and a numeric epoch alike', () => {
    expect(clockTime(TS)).not.toBe('')
    expect(clockTime(1788784325)).not.toBe('')
    expect(isoStamp(TS)).toBe('2026-09-07T14:32:05.123Z')
  })

  it('gives the title the date the visible form cannot carry', () => {
    const full = fullStamp(TS)
    expect(full).not.toBe('')
    expect(full).toMatch(/2026/)
    expect(full.length).toBeGreaterThan(clockTime(TS).length)
  })
})

describe('a turn shows its time only when the host passes one', () => {
  it('renders a real <time> with a machine-readable value', () => {
    render(<AssistantActions text="hi" isLast ts={TS} {...NOOP} />)
    const el = screen.getByText(clockTime(TS))
    expect(el.tagName).toBe('TIME')
    expect(el.getAttribute('dateTime')).toBe(isoStamp(TS))
    expect(el.getAttribute('title')).toBe(fullStamp(TS))
  })

  it('renders no time at all when the stamp is absent', () => {
    const { container } = render(<AssistantActions text="hi" isLast {...NOOP} />)
    expect(container.querySelectorAll('time')).toHaveLength(0)
  })

  it('renders nothing rather than a placeholder for an unreadable stamp', () => {
    const { container } = render(<AssistantActions text="hi" isLast ts="not a date" {...NOOP} />)
    expect(container.querySelectorAll('time')).toHaveLength(0)
    expect(container.textContent).not.toMatch(/NaN|Invalid/)
  })

  it('shows it on a USER turn too, on that turn’s own edge', () => {
    render(<UserActions text="hi" ts={TS} onEdit={vi.fn()} onFork={vi.fn()} />)
    expect(screen.getByText(clockTime(TS)).tagName).toBe('TIME')
  })
})

describe('🔑 the stamp persists while the buttons keep their hover reveal', () => {

  it('the stamp is NOT inside the hover-reveal wrapper', () => {
    render(<AssistantActions text="hi" isLast ts={TS} {...NOOP} />)
    const stamp = screen.getByText(clockTime(TS))
    expect(stamp.closest('.opacity-0'), 'the time would only appear on hover').toBeNull()
  })

  it('the buttons ARE inside it, so the row still rests quiet', () => {
    render(<AssistantActions text="hi" isLast ts={TS} {...NOOP} />)
    const copy = screen.getByRole('button', { name: 'Copy' })
    expect(copy.closest('.opacity-0'), 'the action buttons stopped being hover-revealed').not.toBeNull()
  })

  it('keeps the focus escape hatch on the wrapper that holds the buttons', () => {
    const code = codeOf('features', 'chat', 'MessageActions.tsx')
    const reveal = code.match(/const REVEAL =([\s\S]*?)\n\n/)?.[1] ?? ''
    expect(reveal, 'the REVEAL class list').not.toBe('')
    expect(reveal).toMatch(/opacity-0/)
    expect(reveal).toMatch(/group-hover\/msg:opacity-100/)
    expect(reveal).toMatch(/focus-within:opacity-100/)
  })

  it('uses the type role the design system reserves for a timestamp', () => {
    render(<AssistantActions text="hi" isLast ts={TS} {...NOOP} />)
    const stamp = screen.getByText(clockTime(TS))
    expect(stamp.getAttribute('data-type')).toBe('caption')
    expect(stamp.className).toMatch(/tabular-nums/)
    expect(stamp.className).toMatch(/text-on-surface-low/)
  })
})

describe('the preference actually reaches the transcript', () => {

  it('chat reads the stored preference and gates the stamp on it', () => {
    const code = codeOf('features', 'ChatPage.tsx')
    expect(code.length, 'read ChatPage.tsx').toBeGreaterThan(1000)
    expect(code).toMatch(/useQuery\(\s*'chat:show-timestamps'/)
    expect(code).toMatch(/\.show_timestamps\b/)
    expect(code).toMatch(/showTimestamps \? turn\.ts : undefined/)
    expect(code.match(/ts=\{stampOf\(turn\)\}/g) ?? [], 'both action rows pass the stamp').toHaveLength(2)
  })

  it('both writers bust the reader key, so the toggle changes the transcript at once', () => {
    expect(codeOf('features', 'settings', 'ChatPanel.tsx')).toMatch(/invalidateKeys\('chat:show-timestamps'\)/)
    expect(codeOf('features', 'settings', 'settingsWidgets.tsx')).toMatch(/'chat:show-timestamps'/)
  })

  it('the settings copy already described this, and now it is true', () => {
    const panel = codeOf('features', 'settings', 'ChatPanel.tsx')
    const row = panel.match(/label="Show timestamps" hint="([^"]*)"/)?.[1] ?? ''
    expect(row, 'the Show timestamps row hint').not.toBe('')
    expect(row).toMatch(/time on each message/)
  })
})
