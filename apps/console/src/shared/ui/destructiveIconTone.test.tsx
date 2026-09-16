import { describe, it, expect } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { Trash2 } from 'lucide-react'
import { IconButton } from './IconButton'


const SRC = join(process.cwd(), "src")
const strip = (s: string) => s
  .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/\{\/\*[\s\S]*?\*\/\}/g, (m) => m.replace(/[^\n]/g, ' '))
  .replace(/^(\s*)\/\/.*$/gm, '$1')
const codeOf = (rel: string) => strip(readFileSync(join(SRC, rel), 'utf8'))

describe('IconButton renders the danger tone as a hover tint, not a fill', () => {
  it('a danger button keeps this tier’s resting ink and tints on hover', () => {
    render(<IconButton icon={Trash2} label="Delete thing" tone="danger" />)
    const cls = screen.getByRole('button', { name: 'Delete thing' }).className
    expect(cls).toContain('hover:text-danger')
    expect(cls).toContain('text-on-surface-var')
    expect(cls, 'the sibling tier’s ink must not leak in').not.toContain('text-on-surface-low')
    expect(cls).not.toContain('bg-danger')
    cleanup()
  })

  it('the default is neutral, so no existing call site changes appearance', () => {
    render(<IconButton icon={Trash2} label="Copy thing" />)
    const cls = screen.getByRole('button', { name: 'Copy thing' }).className
    expect(cls).not.toContain('hover:text-danger')
    expect(cls).toContain('hover:text-on-surface')
    cleanup()
  })

  it('`filled` and `active` win over the tone — a selected destructive button is not a pattern here', () => {
    render(<IconButton icon={Trash2} label="Filled danger" tone="danger" filled />)
    const filled = screen.getByRole('button', { name: 'Filled danger' }).className
    expect(filled, 'filled claims the colour').toContain('bg-primary')
    expect(filled).not.toContain('hover:text-danger')
    cleanup()
    render(<IconButton icon={Trash2} label="Active danger" tone="danger" active />)
    const active = screen.getByRole('button', { name: 'Active danger' }).className
    expect(active).toContain('bg-surface-high')
    expect(active).not.toContain('hover:text-danger')
    cleanup()
  })

  it('a disabled danger button still reads as unavailable, not as danger', () => {
    render(<IconButton icon={Trash2} label="Gone" tone="danger" disabled />)
    const cls = screen.getByRole('button', { name: 'Gone' }).className
    expect(cls).toContain('cursor-not-allowed')
    expect(cls).not.toContain('hover:text-danger')
    cleanup()
  })
})

describe('the nine persisted destroys adopted it', () => {
  const ADOPTERS: [string, string][] = [
    ['features/ChatPage.tsx', 'Delete chat'],
    ['features/tasks/TaskDetail.tsx', 'Delete comment'],
    ['features/files/comments/CommentLayer.tsx', 'Remove comment'],
    ['features/knowledge/ReportsPage.tsx', 'Delete ${report.name}'],
    ['features/notifications/NotificationsPage.tsx', 'Delete: ${subject}'],
    ['features/settings/ChatPanel.tsx', 'Delete ${t.name}'],
    ['features/chat/SdlcProgressCard.tsx', 'Click again to delete'],
    ['features/loops/LoopsListPage.tsx', 'Delete loop'],
  ]

  for (const [rel, label] of ADOPTERS) {
    it(`${rel} passes tone="danger" on its destructive IconButton`, () => {
      const code = codeOf(rel)
      const tags = [...code.matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      const tag = tags.find((t) => t.includes(label))
      expect(tag, `found the IconButton labelled ${label}`).toBeTruthy()
      expect(tag!, `${label} must declare the danger tone`).toMatch(/tone="danger"/)
    })
  }

  it('🔑 the two hand-rolled copies are GONE from their className', () => {
    for (const rel of ['features/ChatPage.tsx', 'features/tasks/TaskDetail.tsx']) {
      const code = codeOf(rel)
      const tags = [...code.matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      const offenders = tags.filter((t) => /className="[^"]*hover:text-danger/.test(t))
      expect(offenders, `${rel} still hand-rolls the danger hover:\n${offenders.join('\n')}`).toEqual([])
    }
  })

  it('🪤 the ARMED className is deliberately KEPT — it is a different signal', () => {
    for (const rel of ['features/chat/SdlcProgressCard.tsx', 'features/loops/LoopsListPage.tsx']) {
      expect(codeOf(rel), `${rel} keeps its armed tint`).toMatch(/\? 'text-danger'/)
    }
  })
})

describe('🔴 the composer controls were ALREADY tinted, and that decision stands', () => {
  const ALREADY_TINTED = [
    'Cancel upload', 'Remove file', 'Remove knowledge reference', 'Cancel queued message', 'Remove paste',
  ]
  for (const label of ALREADY_TINTED) {
    it(`ChatPage — "${label}" keeps its danger tint, now via the prop`, () => {
      const tags = [...codeOf('features/ChatPage.tsx').matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      const tag = tags.find((t) => t.includes(label))
      expect(tag, `found the IconButton for ${label}`).toBeTruthy()
      expect(tag!, 'appearance preserved through the prop').toMatch(/tone="danger"/)
    })
  }
})

describe('the two that stay neutral, because they are not destroys', () => {
  const NEUTRAL: [string, string][] = [
    ['features/projects/ProjectsSection.tsx', 'Clear workspace'],
    ['features/settings/ModelsPanel.tsx', 'from chain'],
  ]
  for (const [rel, label] of NEUTRAL) {
    it(`${rel} — "${label}" is NOT tinted`, () => {
      const code = codeOf(rel)
      const tags = [...code.matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      const tag = tags.find((t) => t.includes(label))
      expect(tag, `found the IconButton for ${label}`).toBeTruthy()
      expect(tag!, `${label} changes nothing stored`).not.toMatch(/tone="danger"/)
      expect(tag!, 'and it is not hand-rolling one').not.toMatch(/hover:text-danger/)
    })
  }
})

describe('VACUITY: the sweep is measuring something', () => {
  const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

  it('IconButton still has many call sites, and the danger ones are the minority', () => {
    let total = 0
    let danger = 0
    for (const abs of walk(SRC)) {
      const code = strip(readFileSync(abs, 'utf8'))
      const tags = [...code.matchAll(/<IconButton[\s\S]{0,420}?\/>/g)].map((m) => m[0])
      total += tags.length
      danger += tags.filter((t) => /tone="danger"/.test(t)).length
    }
    expect(total, 'IconButton call sites').toBeGreaterThanOrEqual(30)
    expect(danger, 'danger-toned sites').toBeGreaterThanOrEqual(8)
    expect(danger, 'danger stays the exception, or the colour stops meaning anything')
      .toBeLessThan(total / 3)
  })

  it('the sibling tier still declares the tone this one borrowed the rule from', () => {
    expect(codeOf('shared/ui/SquareIconButton.tsx')).toMatch(/tone\?: 'neutral' \| 'danger'/)
  })
})
