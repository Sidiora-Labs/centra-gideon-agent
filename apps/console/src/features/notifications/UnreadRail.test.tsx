import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { UnreadRail } from './UnreadRail'


describe('UnreadRail', () => {
  it('renders the rail for an UNREAD row, in the kind tone', () => {
    const { container } = render(<UnreadRail tone="var(--color-warn)" acked={false} />)
    const rail = container.firstElementChild as HTMLElement
    expect(rail.tagName).toBe('SPAN')
    expect(rail.style.background).toBe('var(--color-warn)')
  })

  it('renders NOTHING for a read row', () => {
    const { container } = render(<UnreadRail tone="var(--color-primary)" acked />)
    expect(container.firstElementChild).toBeNull()
  })

  it('never sets box-shadow — the property the focus ring needs', () => {
    const { container } = render(<UnreadRail tone="var(--color-ok)" acked={false} />)
    const rail = container.firstElementChild as HTMLElement
    expect(rail.style.boxShadow, 'the rail must not use box-shadow').toBe('')
    const code = readFileSync(join(process.cwd(), "src/features/notifications/UnreadRail.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(code).not.toMatch(/boxShadow|box-shadow:/)
  })

  it('is decorative, so it is hidden from assistive tech', () => {
    const { container } = render(<UnreadRail tone="var(--color-primary)" acked={false} />)
    expect((container.firstElementChild as HTMLElement).getAttribute('aria-hidden')).toBe('true')
  })

  it('follows the row radius it is given', () => {
    const lg = render(<UnreadRail tone="t" acked={false} />).container.firstElementChild as HTMLElement
    const md = render(<UnreadRail tone="t" acked={false} radius="md" />).container.firstElementChild as HTMLElement
    expect(lg.className).toContain('rounded-l-lg')
    expect(md.className).toContain('rounded-l-md')
  })
})

describe('both notification rows render through it, and neither carries an inline shadow', () => {
  const read = (rel: string) => readFileSync(join(process.cwd(), "src", rel), 'utf8')
  const ROWS = ['features/notifications/NotificationsPage.tsx', 'shared/ui/NotificationBell.tsx']

  it('the rail has exactly one implementation', () => {
    for (const rel of ROWS) {
      expect(read(rel), `${rel} must render the shared rail`).toMatch(/<UnreadRail /)
    }
    expect(read('features/notifications/notificationMeta.ts')).not.toMatch(/export function unreadRail/)
  })

  it('neither row sets a style that would replace the ring composite', () => {
    for (const rel of ROWS) {
      const code = read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const wrappers = [...code.matchAll(/<motion\.div[\s\S]{0,1200}?onClick=\{onOpen\}/g)]
      expect(wrappers.length, `${rel}: the row wrapper`).toBeGreaterThanOrEqual(1)
      for (const w of wrappers) expect(w[0], `${rel} must not restore an inline box-shadow`).not.toMatch(/style=\{unreadRail/)
    }
  })
})
