import ts from 'typescript'
import { nodes } from '../testing/sourceOwners'
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
    expect(files.length, 'widgets using RowAction').toBeGreaterThanOrEqual(WIDGETS.length)
    for (const file of files.filter(file => !WIDGETS.some(([rel]) => file === join(SRC, rel)))) {
      const sites = nodes(readFileSync(file, 'utf8'), node => (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && node.tagName.getText() === 'RowAction')
      expect(sites.length).toBeGreaterThan(0)
      for (const site of sites) {
        if (!ts.isJsxOpeningElement(site) && !ts.isJsxSelfClosingElement(site)) throw new Error('Unknown RowAction node')
        const name = site.attributes.properties.find(prop => ts.isJsxAttribute(prop) && prop.name.getText() === 'ariaLabel')
        expect(name, `${file}: new row control requires its own subject`).toBeDefined()
        expect(name?.getText()).toMatch(/\$\{[^}]+\}/)
      }
    }
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
    expect(code).toMatch(/label=\{`Delete project: \$\{project\.name\}`\} title="Delete project"/)
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

  function rowActions() {
    const out: { rel: string; text: string; named: boolean }[] = []
    for (const abs of walkTsx(SRC)) {
      const source = readFileSync(abs, 'utf8')
      for (const node of nodes(source, node => ts.isJsxElement(node) && node.openingElement.tagName.getText() === 'Button')) {
        if (!ts.isJsxElement(node)) continue
        const text = node.children.map(child => ts.isJsxText(child) ? child.text.trim() : '{}').join('').trim()
        if (!/^[A-Za-z][\w' ,.:—–-]{1,58}$/.test(text)) continue
        let owner: ts.Node | undefined = node.parent
        while (owner && !ts.isArrowFunction(owner)) owner = owner.parent
        if (!owner || !ts.isArrowFunction(owner) || !ts.isCallExpression(owner.parent) || !ts.isPropertyAccessExpression(owner.parent.expression) || owner.parent.expression.name.text !== 'map') continue
        const item = owner.parameters[0]?.name.getText()
        const properties = node.openingElement.attributes.properties
        const action = properties.find(prop => ts.isJsxAttribute(prop) && prop.name.getText() === 'onClick')
        if (!item || !action) continue
        const references = nodes(action.getText(), child => ts.isIdentifier(child) && child.text === item)
        if (!references.length) continue
        out.push({ rel: abs.slice(SRC.length + 1), text, named: properties.some(prop => ts.isJsxAttribute(prop) && prop.name.getText() === 'ariaLabel') })
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
