import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { PanelRight } from 'lucide-react'
import { HeaderControl } from './HeaderActions'
import { IconButton } from './IconButton'
import { TileButton } from './TileButton'


describe('HeaderControl announces the state its colour already showed', () => {
  it('is pressed when active', () => {
    render(<HeaderControl icon={PanelRight} label="Activity" active onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Activity' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('is unpressed when explicitly inactive', () => {
    render(<HeaderControl icon={PanelRight} label="Activity" active={false} onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Activity' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('says nothing at all when the control is not a toggle', () => {
    render(<HeaderControl icon={PanelRight} label="Run build" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Run build' }).hasAttribute('aria-pressed')).toBe(false)
  })
})

describe('IconButton does the same, and keeps what it already announced', () => {
  it('is pressed when active', () => {
    render(<IconButton icon={PanelRight} label="Optimize prompt" active onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Optimize prompt' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('omits the attribute for a plain icon action', () => {
    render(<IconButton icon={PanelRight} label="Close" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Close' }).hasAttribute('aria-pressed')).toBe(false)
  })

  it('still carries its name and its disabled contract', () => {
    render(<IconButton icon={PanelRight} label="Optimize prompt" disabled disabledReason="Type something first" onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Optimize prompt' })
    expect(el.getAttribute('aria-disabled')).toBe('true')
    expect(el.getAttribute('title')).toBe('Optimize prompt — Type something first')
  })
})

describe('TileButton — the card-shaped member of the family', () => {
  it('is pressed when the tile is the selected one', () => {
    render(<TileButton active onClick={vi.fn()} ariaLabel="Retro Terminal">tile</TileButton>)
    expect(screen.getByRole('button', { name: 'Retro Terminal' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('is unpressed when it is a selectable tile that is not selected', () => {
    render(<TileButton active={false} onClick={vi.fn()} ariaLabel="Gideon Arcade">tile</TileButton>)
    expect(screen.getByRole('button', { name: 'Gideon Arcade' }).getAttribute('aria-pressed')).toBe('false')
  })

  it('says nothing when the tile is not a selection at all', () => {
    render(<TileButton onClick={vi.fn()} ariaLabel="open me">tile</TileButton>)
    expect(screen.getByRole('button', { name: 'open me' }).hasAttribute('aria-pressed')).toBe(false)
  })
})

describe("the design panel's own two hand-rolled selectors", () => {
  const SRC = join(process.cwd(), "src")
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it('the scheme tile announces which scheme is on', () => {
    expect(read('features/settings/DesignPanel.tsx')).toMatch(/<button type="button" onClick=\{onPick\} aria-pressed=\{active\}/)
  })

  it('the token select pill announces its state AND names its group', () => {
    const src = read('shared/ui/TokenControls.tsx')
    expect(src, 'the bare value is not a name — "dm-sans" told nobody it was Font family')
      .toMatch(/aria-label=\{`\$\{token\.label\}: \$\{opt\}`\}/)
    expect(src).toMatch(/aria-pressed=\{on\}/)
  })

  it('the Mode row it converged onto is unchanged', () => {
    expect(read('features/settings/DesignPanel.tsx')).toMatch(/aria-label=\{`Mode: \$\{m\.label\}`\} aria-pressed=\{on\}/)
  })
})

describe('an `active` that can never be true is deleted, not announced', () => {
  const SRC = join(process.cwd(), "src")
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  it('the artifacts grid no longer threads a hard-coded selection', () => {
    for (const rel of ['features/artifacts/ArtifactCard.tsx', 'features/artifacts/ArtifactGrid.tsx', 'features/artifacts/ArtifactsSection.tsx']) {
      expect(read(rel), `${rel} still carries the inert prop`).not.toMatch(/activeSlug|active=\{a\.slug/)
    }
  })

  it('and the card asks TileButton for no state', () => {
    expect(read('features/artifacts/ArtifactCard.tsx')).toMatch(/<TileButton onClick=\{\(\) => onOpen\(art\)\} title=\{art\.name\} ariaLabel=\{art\.name\}/)
  })
})

describe('the population this reaches, so the primitives were the right place', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  const callSites = (prim: string) =>
    walk(SRC).flatMap((abs) =>
      [...readFileSync(abs, 'utf8').matchAll(new RegExp(`<${prim}\\b[\\s\\S]{0,400}?/>`, 'g'))]
        .filter((m) => /\bactive=/.test(m[0])))

  it('HeaderControl has 14 active call sites', () => {
    expect(callSites('HeaderControl').length).toBeGreaterThanOrEqual(14)
  })

  it('FilterChip has 8, and now announces them', () => {
    expect(callSites('FilterChip').length).toBeGreaterThanOrEqual(8)
    const src = readFileSync(join(SRC, 'features/knowledge/KnowledgeListPage.tsx'), 'utf8')
    expect(src).toMatch(/<button type="button" onClick=\{onClick\} aria-pressed=\{active\}/)
  })

  it('IconButton has 2 — small, and one of them is a recording state', () => {
    expect(callSites('IconButton').length).toBeGreaterThanOrEqual(2)
  })

  it('each primitive binds the attribute to its own `active` prop', () => {
    for (const rel of ['shared/ui/HeaderActions.tsx', 'shared/ui/IconButton.tsx', 'shared/ui/SquareIconButton.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
      const bind = src.match(/aria-pressed=\{([^}]*)\}/)
      expect(bind, `${rel} must bind aria-pressed`).not.toBeNull()
      const expr = bind![1]
      expect(expr, `${rel}: aria-pressed must read its own state prop (active/on)`)
        .toMatch(/\b(active|on)\b/)
      if (/ariaExpanded\?:/.test(src)) {
        expect(expr, `${rel}: aria-pressed must defer to ariaExpanded when the primitive has one`)
          .toMatch(/ariaExpanded/)
      }
    }
  })
})

describe('a pressed header control names the action, not the state', () => {
  const SRC = join(process.cwd(), "src")
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
  const code = (rel: string) => read(rel)
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')

  it('renders one stable name whether it is on or off', () => {
    const { unmount } = render(<HeaderControl icon={PanelRight} label="Favorite" active onClick={vi.fn()} />)
    const on = screen.getByRole('button', { name: 'Favorite' })
    expect(on.getAttribute('aria-pressed')).toBe('true')
    unmount()
    render(<HeaderControl icon={PanelRight} label="Favorite" active={false} onClick={vi.fn()} />)
    const off = screen.getByRole('button', { name: 'Favorite' })
    expect(off.getAttribute('aria-pressed')).toBe('false')
  })

  it("knowledge's three two-state toggles carry constant labels", () => {
    const src = code('features/knowledge/KnowledgeDetail.tsx')
    for (const [icon, label] of [['Star', 'Favorite'], ['Pin', 'Pin'], ['Archive', 'Archive']]) {
      expect(src, `${icon} toggle should pass a constant label="${label}"`)
        .toMatch(new RegExp(`icon=\\{${icon}\\}\\s+label="${label}"`))
    }
  })

  it('no header control restates its state in its label', () => {
    const src = code('features/knowledge/KnowledgeDetail.tsx')
    for (const participle of ['Favorited', 'Pinned', 'Archived']) {
      expect(src, `"${participle}" is a state, not an action — aria-pressed already carries it`)
        .not.toMatch(new RegExp(`label=\\{[^}]*'${participle}'`))
    }
  })

  it('the read-state cycle is left alone — three states need an action label', () => {
    expect(code('features/knowledge/KnowledgeDetail.tsx')).toMatch(/'Reading — mark read'/)
  })

  it('the scanned source is real (guard against a vacuous pass)', () => {
    const src = code('features/knowledge/KnowledgeDetail.tsx')
    expect(src).toContain('<HeaderControl')
    expect(src).toContain('active={!!full.favorited}')
    expect(code('shared/ui/activeMeansPressed.test.tsx')).not.toContain(['quieter', 'fault'].join(' '))
  })
})
