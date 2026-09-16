import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { TileButton } from '../ui/TileButton'
import { IconButton } from '../ui/IconButton'
import { Trash2 } from 'lucide-react'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const codeOf = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')


describe('a tile whose content is a document needs an explicit name', () => {
  it('TileButton takes ariaLabel, and it wins over the content', () => {
    render(
      <TileButton title="Design Notes" ariaLabel="Design Notes">
        <div># Verdant Hollow — Design Notes ## Core loop - Explore procedurally-generated forests…</div>
      </TileButton>,
    )
    const b = screen.getByRole('button', { name: 'Design Notes' })
    expect(b.getAttribute('title')).toBe('Design Notes')
  })

  it('without it the name IS the content — the defect, pinned', () => {
    render(<TileButton title="Design Notes"><div># Verdant Hollow — Design Notes ## Core loop</div></TileButton>)
    const b = screen.getByRole('button', { name: /Verdant Hollow/ })
    expect(b, 'title does not win over content').toBeTruthy()
  })

  it('the artifact card passes the artifact name', () => {
    expect(codeOf('features/artifacts/ArtifactCard.tsx'))
      .toMatch(/<TileButton onClick=\{\(\) => onOpen\(art\)\} title=\{art\.name\} ariaLabel=\{art\.name\}/)
    expect(codeOf('features/artifacts/ArtifactCard.tsx'), 'and it claims no selection state')
      .not.toMatch(/active=\{active\}/)
  })
})

describe('an icon button can carry a row name without a paragraph tooltip', () => {
  it('IconButton takes a title override, like SquareIconButton already did', () => {
    render(<IconButton icon={Trash2} label="Delete: Loop progress — cycle 4 finished" title="Delete" onClick={vi.fn()} />)
    const b = screen.getByRole('button', { name: 'Delete: Loop progress — cycle 4 finished' })
    expect(b.getAttribute('title'), 'the hover hint stays the bare verb').toBe('Delete')
  })

  it('defaults the tooltip to the label when no override is given', () => {
    render(<IconButton icon={Trash2} label="Delete" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete' }).getAttribute('title')).toBe('Delete')
  })

  it('the override composes with disabledReason rather than replacing it', () => {
    render(<IconButton icon={Trash2} label="Delete: a row" title="Delete" disabled disabledReason="Nothing selected" onClick={vi.fn()} />)
    expect(screen.getByRole('button', { name: 'Delete: a row' }).getAttribute('title')).toBe('Delete — Nothing selected')
  })
})

describe("the notification row actions name their row, and stay bounded", () => {
  const code = codeOf('features/notifications/NotificationsPage.tsx')

  it('all four actions name the row through the shared helper', () => {
    expect(code, 'the row subject is computed once')
      .toMatch(/const subject = rowSubject\(\[n\.title, firstLine\(n\.body \?\? ''\)\]\)/)
    for (const verb of ['Investigate in chat', 'Mark unread', 'Mark read', 'Delete']) {
      expect(code, `${verb} must name its row`).toMatch(new RegExp(`\`${verb}: \\$\\{subject\\}\``))
    }
    expect(code, 'the row hit target shares the actions\' subject').toMatch(/<RowHitTarget label=\{subject\} \/>/)
  })

  it("the bell's shade actions name their row too, and bind the subject once", () => {
    const bell = codeOf('shared/ui/NotificationBell.tsx')
    expect(bell, 'the subject is computed once for the row and both actions')
      .toMatch(/const subject = rowSubject\(\[n\.title, firstLine\(n\.body \?\? ''\)\]\)/)
    for (const verb of ['Mark read', 'Delete']) {
      expect(bell, `${verb} must name its row`).toMatch(new RegExp(`aria-label=\\{\`${verb}: \\$\\{subject\\}\`\\}`))
    }
    expect(bell, 'the row itself shares that subject').toMatch(/<RowHitTarget label=\{subject\} \/>/)
    expect(bell).toMatch(/title="Mark read"/)
    expect(bell).toMatch(/title="Delete"/)
    expect(bell, 'a disk delete must not be called a dismiss').not.toMatch(/[Dd]ismiss/)
  })

  it('the composition and its cap live in the shared helper, not here', () => {
    expect(code).toMatch(/rowSubject\(\[n\.title, firstLine\(n\.body \?\? ''\)\]\)/)
    expect(code, 'a local re-implementation is the drift this closed').not.toMatch(/function rowName/)
    expect(code, 'and its number with it').not.toMatch(/full\.length > 55/)
  })

  it('the tooltips stay the bare verbs', () => {
    expect(code).toMatch(/title="Mark unread"/)
    expect(code).toMatch(/title="Mark read"/)
    expect(code).toMatch(/title="Delete"/)
  })

  it('InvestigateButton keeps its bare-verb default for single-instance use', () => {
    const inv = codeOf('shared/ui/InvestigateButton.tsx')
    expect(inv).toMatch(/label=\{label \?\? 'Investigate in chat'\}/)
    expect(inv).toMatch(/title="Investigate in chat"/)
  })
})




function iconOnlyButtons(): string[] {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })
  const out: string[] = []
  for (const abs of walk(SRC)) {
    const lines = readFileSync(abs, 'utf8').split('\n')
    lines.forEach((line, i) => {
      if (!line.includes('<Button')) return
      const blob = lines.slice(i, i + 6).join('\n')
      const start = blob.indexOf('<Button')
      let depth = 0
      let end = -1
      for (let k = start; k < blob.length; k++) {
        const c = blob[k]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) { end = k; break }
      }
      if (end === -1 || blob[end - 1] === '/') return
      const attrs = blob.slice(start + 7, end)
      const close = blob.indexOf('</Button>', end)
      if (close === -1) return
      const inner = blob.slice(end + 1, close).replace(/\s+/g, ' ').trim()
      if (/ariaLabel|aria-label|title=/.test(attrs)) return
      if (/^<[A-Z]\w+[^>]*\/>$/.test(inner)) out.push(`${abs.slice(SRC.length + 1)}:${i + 1} — ${inner}`)
    })
  }
  return out
}

describe('a Button whose whole body is an icon carries a name', () => {
  it('none is left unnamed anywhere in the tree', () => {
    const offenders = iconOnlyButtons()
    expect(offenders, `these announce as bare "button":\n${offenders.join('\n')}`).toEqual([])
  })

  it('the scan is not vacuous — it still finds the shape when the name is removed', () => {
    const blob = '<Button size="sm" onClick={() => go()}><Trash2 size={14} /></Button>'
    const start = blob.indexOf('<Button')
    let depth = 0
    let end = -1
    for (let k = start; k < blob.length; k++) {
      const c = blob[k]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) { end = k; break }
    }
    expect(end, 'the walk must pass the > inside the arrow function').toBeGreaterThan(blob.indexOf('go()'))
  })

  it('the three named sites keep their names', () => {
    const read = (rel: string) => readFileSync(join(process.cwd(), "src", rel), 'utf8')
    expect(read('features/knowledge/KnowledgeListPage.tsx'), 'the destructive one, through the shared cap')
      .toMatch(/ariaLabel=\{`Delete intent: \$\{rowSubject\(\[it\.goal \|\| it\.id\], 40\)\}`\}/)
    expect(read('features/settings/MemoryPanel.tsx')).toMatch(/ariaLabel="Reload the audit log"/)
    expect(read('features/projects/ProjectsSection.tsx')).toMatch(/ariaLabel="Save the project name"/)
  })

  it('Button can carry a name at all, and documents it', () => {
    expect(readFileSync(join(process.cwd(), "src/shared/ui/Button.tsx"), 'utf8')).toMatch(/aria-label=\{ariaLabel\}/)
    expect(readFileSync(join(process.cwd(), "src/shared/ui/Button.doc.ts"), 'utf8')).toMatch(/name: 'ariaLabel'/)
  })
})

describe('a hyphenated aria prop on a kit component is a dropped name', () => {
  const KIT = ['Button', 'TileButton', 'Segmented', 'HeaderSegmented', 'QuietButton', 'TextInput',
    'TextArea', 'SearchField', 'Slider', 'HeaderControl', 'IconButton']

  const walk = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walk(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  it('no call site passes one', () => {
    const SRC = join(process.cwd(), "src")
    const offenders: string[] = []
    for (const abs of walk(SRC)) {
      readFileSync(abs, 'utf8').split('\n').forEach((line, i) => {
        for (const c of KIT) {
          const m = new RegExp(`<${c}\\b([^>]*)`).exec(line)
          if (m && /\saria-[a-z]+=/.test(m[1])) offenders.push(`${abs.slice(SRC.length + 1)}:${i + 1} — <${c} ${/\s(aria-[a-z]+)=/.exec(m[1])?.[1]}>`)
        }
      })
    }
    expect(offenders, `a hyphenated aria prop here is dropped in silence:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the two week arrows carry the forwarded prop instead', () => {
    const src = readFileSync(join(process.cwd(), "src/features/triggers/WeekGridView.tsx"), 'utf8')
    expect(src).toMatch(/ariaLabel="Previous week"/)
    expect(src).toMatch(/ariaLabel="Next week"/)
    expect(src, 'and not the form the primitive ignores').not.toMatch(/<Button[^>]*aria-label=/)
  })

  it("the marketplace picker names itself", () => {
    const src = readFileSync(join(process.cwd(), "src/features/skills/SkillsPage.tsx"), 'utf8')
    expect(src).toMatch(/setMarketplace\(e\.target\.value\)\} aria-label="Marketplace"/)
  })
})

describe('the detector checkboxes keep their visible key inside the name', () => {
  it('does not de-underscore the key it is naming', () => {
    const code = codeOf('features/knowledge/SourceCreatePage.tsx')
    expect(code, 'a name that rewrites its own visible label stops containing it (WCAG 2.5.3)')
      .not.toMatch(/ariaLabel=\{`Run the \$\{d\.replace/)
  })

  it('names the action AND carries the raw key', () => {
    const code = codeOf('features/knowledge/SourceCreatePage.tsx')
    expect(code).toMatch(/ariaLabel=\{`Run the \$\{d\} detector`\}/)
  })

  it('the visible label is still the bare key in mono — the name adds, it does not replace', () => {
    expect(codeOf('features/knowledge/SourceCreatePage.tsx'))
      .toMatch(/<span className="font-mono">\{d\}<\/span>/)
  })
})
