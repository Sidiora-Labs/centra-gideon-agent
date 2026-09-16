import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Pencil } from 'lucide-react'
import { QuietButton } from './QuietButton'
import { SquareIconButton } from './SquareIconButton'


describe('QuietButton announces expansion when it is a disclosure', () => {
  it('emits the state when asked', () => {
    render(<QuietButton onClick={vi.fn()} ariaExpanded>View</QuietButton>)
    expect(screen.getByRole('button', { name: 'View' }).getAttribute('aria-expanded')).toBe('true')
  })

  it('emits false when closed, so the state is unambiguous', () => {
    render(<QuietButton onClick={vi.fn()} ariaExpanded={false}>View</QuietButton>)
    expect(screen.getByRole('button', { name: 'View' }).getAttribute('aria-expanded')).toBe('false')
  })

  it('says nothing for a plain quiet action', () => {
    render(<QuietButton onClick={vi.fn()}>Download</QuietButton>)
    expect(screen.getByRole('button', { name: 'Download' }).hasAttribute('aria-expanded')).toBe(false)
  })

  it('keeps its title and its quiet geometry', () => {
    render(<QuietButton onClick={vi.fn()} title="Download the findings log">Download</QuietButton>)
    const el = screen.getByRole('button', { name: 'Download' })
    expect(el.getAttribute('title')).toBe('Download the findings log')
    expect(el.className).toMatch(/h-7/)
  })
})

describe('SquareIconButton announces the tint it was already showing', () => {
  it('is pressed when on', () => {
    render(<SquareIconButton icon={Pencil} label="Edit" on onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Edit' }).getAttribute('aria-pressed')).toBe('true')
  })

  it('omits the attribute for a plain icon action', () => {
    render(<SquareIconButton icon={Pencil} label="Delete" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete' }).hasAttribute('aria-pressed')).toBe(false)
  })

  it('keeps the disabled contract cycle 119 gave it', () => {
    render(<SquareIconButton icon={Pencil} label="Edit" disabled disabledReason="Test the connection first" onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Edit' })
    expect(el.getAttribute('aria-disabled')).toBe('true')
    expect(el.getAttribute('title')).toBe('Edit — Test the connection first')
  })
})

describe('the call sites, classified per site', () => {
  const SRC = join(process.cwd(), "src")
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')

  const DISCLOSURES: [string, string][] = [
    ['features/ChatPage.tsx', 'open'],
    ['features/artifacts/ArtifactViewer.tsx', 'comparing'],
    ['features/workflows/WorkflowRunDetail.tsx', 'workspaceOpen'],
    ['features/workflows/WorkflowRunDetail.tsx', 'outboxOpen'],
    ['features/workflows/WorkflowRunDetail.tsx', 'introspectOpen'],
    ['features/workflows/WorkflowRunDetail.tsx', 'steerOpen'],
  ]

  for (const [rel, state] of DISCLOSURES) {
    it(`${rel.split('/').pop()} passes ariaExpanded={${state}}`, () => {
      expect(read(rel)).toContain(`ariaExpanded={${state}}`)
    })
  }

  it('the two Edit buttons announce expansion, because they open a form', () => {
    expect(read('features/settings/ModelBackends.tsx'))
      .toMatch(/<SquareIconButton label="Edit"[^\n]*ariaExpanded=\{editing\}/)
    expect(read('features/settings/MultiInstanceCard.tsx'))
      .toMatch(/ariaExpanded=\{editing && props\.length > 0\}/)
    for (const rel of ['features/settings/ModelBackends.tsx', 'features/settings/MultiInstanceCard.tsx']) {
      expect(read(rel), `${rel}: an Edit that opens a form is not a toggle`)
        .not.toMatch(/<SquareIconButton label="Edit"[^\n]*\bon=\{editing\}/)
    }
  })

  it('a show/hide-secret button stays silent, because its NAME carries the state', () => {
    for (const rel of ['features/settings/ModelBackends.tsx', 'features/settings/ProviderConfigForm.tsx']) {
      const src = read(rel)
      const at = src.search(/<SquareIconButton label=\{show(Secret)? \? 'Hide' : 'Show'\}/)
      expect(at, `${rel} must still have the name-flipping secret toggle`).toBeGreaterThan(-1)
      expect(src.slice(at, at + 200), 'a name that flips does not need `on` as well').not.toMatch(/\bon=\{/)
    }
  })
})


describe('SquareIconButton asks the right question of the right caller', () => {
  it('a disclosure announces expansion and NOT pressedness', () => {
    render(<SquareIconButton icon={Pencil} label="Configure" ariaExpanded onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Configure' })
    expect(el.getAttribute('aria-expanded')).toBe('true')
    expect(el.hasAttribute('aria-pressed'), 'both states is one state too many').toBe(false)
  })

  it('closed is announced too, so the state is never ambiguous', () => {
    render(<SquareIconButton icon={Pencil} label="Configure" ariaExpanded={false} onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Configure' }).getAttribute('aria-expanded')).toBe('false')
  })

  it('BOTH props at once: the disclosure wins and pressedness is suppressed', () => {
    render(<SquareIconButton icon={Pencil} label="Both" on ariaExpanded onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Both' })
    expect(el.getAttribute('aria-expanded')).toBe('true')
    expect(el.hasAttribute('aria-pressed'), 'a control claiming both states claims one falsely').toBe(false)
  })

  it('a true toggle keeps aria-pressed, and claims no expansion', () => {
    render(<SquareIconButton icon={Pencil} label="Pin" on onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Pin' })
    expect(el.getAttribute('aria-pressed')).toBe('true')
    expect(el.hasAttribute('aria-expanded'), 'a pin reveals nothing').toBe(false)
  })

  it('the coral tint follows EITHER — the fix moves no pixels', () => {
    const { container: a } = render(<SquareIconButton icon={Pencil} label="A" on onClick={vi.fn()} />)
    const { container: b } = render(<SquareIconButton icon={Pencil} label="B" ariaExpanded onClick={vi.fn()} />)
    const cls = (c: HTMLElement) => c.querySelector('button')!.className.replace(/\s+/g, ' ')
    expect(cls(a)).toContain('text-primary')
    expect(cls(b), 'an expanded disclosure is lit exactly like a pressed toggle').toContain('text-primary')
    expect(a.querySelector('button')!.getAttribute('style'))
      .toBe(b.querySelector('button')!.getAttribute('style'))
  })

  it('a plain action claims neither', () => {
    render(<SquareIconButton icon={Pencil} label="Delete" onClick={vi.fn()} />)
    const el = screen.getByRole('button', { name: 'Delete' })
    expect(el.hasAttribute('aria-pressed')).toBe(false)
    expect(el.hasAttribute('aria-expanded')).toBe(false)
  })
})

describe('the SquareIconButton state family is classified, all eleven of it', () => {
  const SRC = join(process.cwd(), "src")
  const walkTsx = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkTsx(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  function stateBearing() {
    const out: { rel: string; kind: 'disclosure' | 'toggle' }[] = []
    for (const abs of walkTsx(SRC)) {
      const src = readFileSync(abs, 'utf8')
      for (const m of src.matchAll(/<SquareIconButton\b/g)) {
        let depth = 0, end = -1
        for (let i = m.index! + m[0].length; i < src.length; i++) {
          const c = src[i]
          if (c === '{') depth++
          else if (c === '}') depth--
          else if (c === '>' && depth === 0) { end = i; break }
        }
        if (end < 0) continue
        const tag = src.slice(m.index!, end + 1)
        const on = /\bon=\{/.test(tag), ex = /ariaExpanded=/.test(tag)
        if (!on && !ex) continue
        expect(on && ex, `${abs}: a control may not claim both states`).toBe(false)
        out.push({ rel: abs.slice(SRC.length + 1), kind: ex ? 'disclosure' : 'toggle' })
      }
    }
    return out
  }

  it('is 7 disclosures and 4 toggles — and nothing unclassified', () => {
    const all = stateBearing()
    expect(all.length, 'the state-bearing population').toBe(11)
    expect(all.filter((x) => x.kind === 'disclosure').length, 'disclosures').toBe(7)
    expect(all.filter((x) => x.kind === 'toggle').length, 'toggles').toBe(4)
  })

  it('the four toggles are the ones that reveal nothing', () => {
    const toggles = stateBearing().filter((x) => x.kind === 'toggle').map((x) => x.rel).sort()
    expect(toggles).toEqual([
      'features/ChatPage.tsx',
      'shared/ui/content/ContentSurface.tsx',
      'shared/ui/widget/WidgetFrame.tsx',
      'shared/ui/widget/WidgetFrame.tsx',
    ])
  })

  it('every disclosure is bound to the flag that gates its content', () => {
    const pairs: [string, RegExp][] = [
      ['features/settings/ProviderCard.tsx', /ariaExpanded=\{open\}/],
      ['features/settings/ModelBackends.tsx', /ariaExpanded=\{showModels\}/],
      ['features/settings/ModelBackends.tsx', /ariaExpanded=\{editing\}/],
      ['features/settings/MultiInstanceCard.tsx', /ariaExpanded=\{editing && props\.length > 0\}/],
      ['shared/ui/widget/WidgetFrame.tsx', /ariaExpanded=\{railOpen\}/],
      ['features/ChatPage.tsx', /ariaExpanded=\{open\}/],
      ['shared/ui/content/ContentSurface.tsx', /ariaExpanded=\{exportOpen\}/],
    ]
    for (const [rel, re] of pairs) {
      expect(readFileSync(join(SRC, rel), 'utf8'), `${rel} must bind ${re}`).toMatch(re)
    }
  })
})
