import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { RowAction } from '../../features/dashboard/widgets/kit'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('RowAction carries an explicit accessible name', () => {
  it('renders the composed name, and the visible verb stays short', () => {
    render(<RowAction onClick={() => {}} title="Open to reply" ariaLabel="Reply: Skill: refine-a-skill">Reply</RowAction>)
    const b = screen.getByRole('button', { name: 'Reply: Skill: refine-a-skill' })
    expect(b.textContent, 'the label a sighted user reads is unchanged').toBe('Reply')
    expect(b.getAttribute('title'), 'the tooltip stays the short hint').toBe('Open to reply')
  })

  it('without it, the button falls back to its text — the defect being fixed', () => {
    render(<RowAction onClick={() => {}} title="Open to reply">Reply</RowAction>)
    expect(screen.getByRole('button', { name: 'Reply' })).toBeTruthy()
  })
})

describe('every row-scoped RowAction names its row', () => {
  const WIDGETS: [string, number, number][] = [
    ['features/dashboard/widgets/ActionCenter.tsx', 4, 0],
    ['features/dashboard/widgets/TasksWidget.tsx', 1, 0],
    ['features/dashboard/widgets/PinnedArtifacts.tsx', 1, 0],
    ['features/dashboard/widgets/ActiveWork.tsx', 2, 1],

    ['features/dashboard/widgets/SystemHealth.tsx', 0, 3],
    ['features/dashboard/widgets/OnThisMachine.tsx', 1, 0],
  ]

  for (const [rel, named, singletons] of WIDGETS) {
    it(`${rel.split('/').pop()} names ${named} and leaves ${singletons} singleton(s)`, () => {
      const code = read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      const sites = [...code.matchAll(/<RowAction\b/g)]
      expect(sites.length, `${rel} call sites`).toBe(named + singletons)
      const withName = [...code.matchAll(/ariaLabel=\{`/g)]
      expect(withName.length, `${rel} must compose ${named} name(s) from the row`).toBe(named)
    })
  }

  it('every composed name interpolates a subject, never a bare verb', () => {
    for (const [rel] of WIDGETS) {
      for (const m of read(rel).matchAll(/ariaLabel=\{`([^`]*)`\}/g)) {
        expect(m[1], `${rel}: ${m[1]} must end in the row's subject`).toMatch(/: \$\{[^}]+\}$/)
      }
    }
  })

  it('no OTHER RowAction call site appears without a name — the census is closed', () => {
    const files = walk(SRC).filter((abs) => readFileSync(abs, 'utf8').includes('<RowAction'))
    expect(files.length, 'widgets using RowAction').toBe(WIDGETS.length)
  })
})

describe('the two list surfaces name their row controls too', () => {
  it("#/tasks' selection checkbox says WHICH task", () => {
    const code = read('features/tasks/TasksListPage.tsx')
    expect(code).toMatch(/aria-label=\{`\$\{selected \? 'Deselect' : 'Select'\}: \$\{t\.title\}`\}/)
    expect(code, 'the old shared name must be gone').not.toMatch(/'Deselect task' : 'Select task'/)
  })

  it("#/projects' delete button says WHICH project, and keeps a short tooltip", () => {
    const code = read('features/projects/ProjectsSection.tsx')
    expect(code).toMatch(/label=\{`Delete project: \$\{p\.name\}`\} title="Delete project"/)
  })

  it('the repeated-chip families are left alone on purpose', () => {
    const kn = read('features/knowledge/KnowledgeListPage.tsx')
    expect(kn, 'tag chips share a name because they share a destination').not.toMatch(/aria-label=\{`Tag: /)
  })
})


describe('the hand-rolled row actions a primitive-shaped census could not see', () => {
  const DURABILITY: [string, string][] = [
    ['Preview restore', '${a.name}'],
    ['Merge-restore', '${a.name}'],
    ['CHOICE_LABELS.keep_local', '${c.entity_id}'],
    ['CHOICE_LABELS.take_remote', '${c.entity_id}'],
    ['CHOICE_LABELS.accept_proposal', '${c.entity_id}'],
  ]
  const panel = () => read('features/settings/DurabilityPanel.tsx')

  it.each(DURABILITY)('%s names its row with %s', (verb, subject) => {
    const code = panel()
    const label = verb.startsWith('CHOICE_LABELS')
      ? `ariaLabel={\`\${${verb}}: ${subject}\`}`
      : `ariaLabel={\`${verb}: ${subject}\`}`
    expect(code, `${verb} must name the row it acts on`).toContain(label)
  })

  it('the history pair is named by POSITION, because subject and time both collide', () => {
    const code = panel()
    for (const verb of ['See going back to here', 'See undoing just this']) {
      expect(code, `${verb} needs the row's position`).toContain(
        `ariaLabel={\`${verb}: change \${i + 1} of \${timeline.data!.entries.length} — \${entry.subject}\`}`)
    }
  })

  it('the visible verbs are unchanged — this is a NAME fix, not a relabel', () => {
    const code = panel()
    expect(code).toMatch(/>\s*See going back to here\s*<\/Button>/)
    expect(code).toMatch(/>\s*See undoing just this\s*<\/Button>/)
    expect(code).toMatch(/>\s*Merge-restore\s*<\/Button>/)
    expect(code).toMatch(/>\s*Preview restore\s*<\/Button>/)
    expect(code, 'the conflict choices still render their shared constants')
      .toMatch(/\{CHOICE_LABELS\.take_remote\}/)
    expect(code, 'and the sibling choice, unwrapped once and rendered as source')
      .toMatch(/\{CHOICE_LABELS\.keep_local\}/)
  })

  it('camelCase, because ui/Button spreads no rest', () => {
    expect(panel(), 'the dashed spelling silently vanishes on ui/Button').not.toMatch(/<Button[^>]*aria-label=/)
  })

  const PROVIDER_CONTROLS: [string, string][] = [
    ['Sign in', 'aria-label={`Sign in: ${who}`}'],
    ['Check availability', 'label={`Check availability: ${who}`} title="Check availability"'],
    ['Configure', 'label={`Configure: ${who}`} title="Configure"'],
    ['Test', 'aria-label={`Test: ${channel.name}`}'],
    ['Disconnect', 'aria-label={`Disconnect: ${channel.name}`}'],
    ['Connect', 'aria-label={`Connect: ${channel.name}`}'],
  ]

  it.each(PROVIDER_CONTROLS)('ProviderCard names its %s', (_verb, expected) => {
    expect(read('features/settings/ProviderCard.tsx')).toContain(expected)
  })

  it('the provider subject is the card\'s own visible title, computed once', () => {
    const code = read('features/settings/ProviderCard.tsx')
    expect(code, 'derived from what the card displays, not re-picked per control')
      .toMatch(/const who = ext\.displayName \|\| ext\.name/)
    expect(code, 'and the title the card renders is the same expression')
      .toMatch(/\{ext\.displayName \|\| ext\.name\}/)
    expect(code).not.toMatch(/label="Configure"/)
    expect(code).not.toMatch(/label="Check availability"/)
  })

  const walkTsx = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkTsx(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  function buttonTags(src: string, from: number, to: number) {
    const out: { tag: string; end: number }[] = []
    for (const m of src.matchAll(/<Button\b/g)) {
      if (m.index! < from || m.index! > to) continue
      let depth = 0
      for (let i = m.index! + m[0].length; i < src.length; i++) {
        const c = src[i]
        if (c === '{') depth++
        else if (c === '}') depth--
        else if (c === '>' && depth === 0) { out.push({ tag: src.slice(m.index!, i + 1), end: i }); break }
      }
    }
    return out
  }

  function rowActions() {
    const out: { rel: string; text: string; named: boolean }[] = []
    for (const abs of walkTsx(SRC)) {
      const raw = readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      for (const m of raw.matchAll(/\.map\(\((\w+)(?:,\s*\w+)?\)\s*=>/g)) {
        const item = m[1]
        for (const t of buttonTags(raw, m.index!, m.index! + 3000)) {
          const close = raw.indexOf('</Button>', t.end)
          if (close < 0) continue
          const text = raw.slice(t.end + 1, close).trim()
          if (/\{/.test(text)) continue
          if (!/^[A-Za-z][\w' ,.:—–-]{1,58}$/.test(text)) continue
          const oc = t.tag.indexOf('onClick={')
          if (oc < 0) continue
          let d = 1, j = t.tag.indexOf('{', oc) + 1
          for (; j < t.tag.length && d > 0; j++) { if (t.tag[j] === '{') d++; else if (t.tag[j] === '}') d-- }
          if (!new RegExp(`\\b${item}\\b`).test(t.tag.slice(oc, j))) continue
          out.push({ rel: abs.slice(SRC.length + 1), text, named: /ariaLabel=/.test(t.tag) })
        }
      }
    }
    return out
  }

  it('finds the population, and the five this cycle named are in it', () => {
    const all = rowActions()
    expect(all.length, 'the row-action scan must resolve its population').toBeGreaterThanOrEqual(10)
    const named = all.filter((r) => r.named)
    expect(named.length, 'the named ones').toBeGreaterThanOrEqual(5)
    expect(named.map((r) => r.rel)).toContain('features/settings/DurabilityPanel.tsx')
  })

  it('the panel this cycle fixed has no unnamed row action left', () => {
    const mute = rowActions().filter((r) => !r.named && r.rel === 'features/settings/DurabilityPanel.tsx')
    expect(mute, `still sharing one name across rows:\n${mute.map((m) => m.text).join('\n')}`).toEqual([])
  })

  it('the remainder is a measured ceiling of 5 — classify, do not add', () => {
    const mute = rowActions().filter((r) => !r.named)
    expect(mute.length, `unnamed row-scoped actions:\n${mute.map((m) => `${m.rel} "${m.text}"`).join('\n')}`)
      .toBeLessThanOrEqual(5)
    expect(mute.length, 'and the census must still see the family it bounds').toBeGreaterThan(0)
  })
})
