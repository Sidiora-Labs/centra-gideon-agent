import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import ts from 'typescript'


const PAGES = join(process.cwd(), "src/features")
const read = (rel: string) => readFileSync(join(PAGES, rel), 'utf8')

function elementAt(src: string, at: number): string {
  const tree = ts.createSourceFile('surface.tsx', src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let tag = ''
  const visit = (node: ts.Node) => {
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node))
      && node.getStart(tree) <= at && node.end > at) tag = node.getText(tree)
    ts.forEachChild(node, visit)
  }
  visit(tree)
  return tag
}

function announcesState(tag: string): boolean {
  return /aria-expanded=|aria-pressed=/.test(tag)
    || (/^<(Button|HeaderControl|IconButton|QuietButton)\b/.test(tag) && /ariaExpanded=|ariaPressed=/.test(tag))
    || (/^<(HeaderControl|IconButton)\b/.test(tag) && /\bactive=/.test(tag))
}

function delegatedAnnouncement(src: string, at: number, handler: string, abs: string): boolean {
  const name = /^(on[A-Z]\w*)=/.exec(handler)?.[1]
  const control = elementAt(src, at)
  const component = /^<(\w+)/.exec(control)?.[1]
  if (!name || !component || /^[a-z]/.test(component)) return false
  let definitionSource = src
  const caller = ts.createSourceFile(abs, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  for (const statement of caller.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier)) continue
    const bindings = statement.importClause?.namedBindings
    if (!bindings || !ts.isNamedImports(bindings) || !bindings.elements.some(binding => binding.name.text === component)) continue
    const module = statement.moduleSpecifier.text
    if (!module.startsWith('.')) continue
    const path = join(dirname(abs), module + '.tsx')
    try { definitionSource = readFileSync(path, 'utf8') } catch { return false }
  }
  const tree = ts.createSourceFile('receiver.tsx', definitionSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const definition = tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === component)
  if (!definition) return false
  let announced = false
  const visit = (node: ts.Node) => {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.getText(tree)
      if (tag.includes(`onClick={${name}}`) && announcesState(tag)) announced = true
    }
    ts.forEachChild(node, visit)
  }
  visit(definition)
  return announced
}

const DISCLOSURES: [string, string, string][] = [
  ['agents/AgentDetail.tsx', 'open', 'setOpen((v) => !v)'],
  ['apps/AppsSection.tsx', 'advancedOpen', 'setAdvancedOpen((o) => !o)'],
  ['files/browse/FilePreviews.tsx', 'open', 'setOpen(!open)'],
  ['loops/LoopCockpitPage.tsx', 'open', 'setOpen((v) => !v)'],
  ['schedule/ScheduleForm.tsx', 'open', 'setOpen((v) => !v)'],
  ['settings/DesignPanel.tsx', 'editingColors', 'setEditingColors((v) => !v)'],
  ['tools/ToolInspector.tsx', 'open', 'setOpen((v) => !v)'],
  ['tools/ToolsPage.tsx', 'open', 'setOpen((v) => !v)'],
]

describe('a button that reveals content says that it does', () => {
  for (const [rel, state, toggle] of DISCLOSURES) {
    it(`${rel} announces its expanded state`, () => {
      const src = read(rel)
      const at = src.indexOf(toggle) >= 0 ? src.indexOf(toggle) : rel === 'agents/AgentDetail.tsx' ? src.indexOf('setOpen(value => !value)') : -1
      expect(at, `${rel} must still contain ${toggle}`).toBeGreaterThan(-1)
      const tag = elementAt(src, at)
      expect(tag, 'the attribute must be on the button itself').toMatch(/aria-expanded=\{/)
      expect(tag, `and bound to \`${state}\` — the same flag its content is gated on`)
        .toContain(`aria-expanded={${state}}`)
    })

    it(`${rel} still gates its content on that same flag`, () => {
      expect(read(rel)).toContain(`{${state} && `)
    })
  }

  it('the two canonical adopters still carry it', () => {
    expect(read('chat/ChatActivityPanel.tsx')).toMatch(/aria-expanded=\{open\}/)
    expect(read('code/CodeCockpitPage.tsx')).toMatch(/aria-expanded=\{showEvidence\}/)
  })

  it('a MODE toggle is not given a disclosure promise', () => {
    const diag = read('settings/DiagnosticsPanel.tsx')
    const autoscroll = diag.slice(diag.indexOf('setAutoscroll((v) => !v)'), diag.indexOf('setAutoscroll((v) => !v)') + 240)
    expect(autoscroll, 'autoscroll flips a mode, it does not disclose').not.toMatch(/aria-expanded/)
    const paused = diag.slice(diag.indexOf('setPaused((v) => !v)'), diag.indexOf('setPaused((v) => !v)') + 240)
    expect(paused, 'pause/resume flips a mode too').not.toMatch(/aria-expanded/)
  })

  it('native active controls forward their state to the DOM', () => {
    const header = readFileSync(join(PAGES, '../shared/ui/HeaderActions.tsx'), 'utf8')
    const icon = readFileSync(join(PAGES, '../shared/ui/IconButton.tsx'), 'utf8')
    expect(header).toContain('aria-pressed={ariaExpanded === undefined ? active : undefined}')
    expect(icon).toContain('aria-pressed={active}')
  })

  it('the census is reproducible — every boolean-toggling button is accounted for', () => {
    const walk = (d: string): string[] =>
      readdirSync(d).flatMap((n) => {
        const p = join(d, n)
        if (statSync(p).isDirectory()) return walk(p)
        return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
      })
    const TOGGLE = /onClick=\{\(\) => set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => set\w+\(!\w+\)/g
    const toggles = walk(PAGES).flatMap((abs) => {
      const src = readFileSync(abs, 'utf8')
      return [...src.matchAll(TOGGLE)].map((m) => elementAt(src, m.index!))
    })
    expect(toggles.length, 'the scan must still find the population it was written for').toBeGreaterThanOrEqual(48)
    const silent = toggles.filter(tag => !announcesState(tag))
    expect(silent.length, 'measured backlog — classify a new toggle, do not add to this number')
      .toBeLessThanOrEqual(34)
  })
})

describe('the accordions the boolean-flip census could not see', () => {
  const ACCORDION = /onClick=\{\(\) => set\w+\(\s*[^?\n]+ \? null : [\w.]+\s*\)/g

  const walkPages = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkPages(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  function announcesDisclosure(around: string): boolean {
    return /aria-expanded/.test(around)
      || (/ariaExpanded=/.test(around) && /<Button\b/.test(around))
  }

  it('the two spellings are accepted on the right elements, and only there', () => {
    expect(announcesDisclosure('<Button onClick={() => setOpen(open ? null : id)} ariaExpanded={isOpen}>'),
      'camelCase on ui/Button reaches the DOM').toBe(true)
    expect(announcesDisclosure('<button onClick={() => setOpen(open ? null : id)} aria-expanded={isOpen}>'),
      'the DOM attribute on a raw button').toBe(true)
    expect(announcesDisclosure('<button onClick={() => setOpen(open ? null : id)} ariaExpanded={isOpen}>'),
      'camelCase on a RAW button is dropped by React — still silent').toBe(false)
    expect(announcesDisclosure('<Button onClick={() => setOpen(open ? null : id)}>detail</Button>'),
      'and a Button that says nothing is still silent').toBe(false)
  })

  function accordions() {
    const out: { rel: string; announced: boolean }[] = []
    for (const abs of walkPages(PAGES)) {
      const src = readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      for (const m of src.matchAll(ACCORDION)) {
        const around = elementAt(src, m.index!)
        out.push({ rel: abs.slice(PAGES.length + 1), announced: announcesDisclosure(around) })
      }
    }
    return out
  }

  it('finds the population (not vacuously green)', () => {
    const found = accordions()
    expect(found.length, 'the accordion scan must find its population').toBeGreaterThanOrEqual(4)
    expect(found.map((a) => a.rel)).toContain('settings/NotificationRulesMatrix.tsx')
  })

  it('every one of them announces that it discloses', () => {
    const silent = accordions().filter((a) => !a.announced).map((a) => a.rel)
    expect(silent, `an accordion that reveals a panel and says nothing:\n${silent.join('\n')}`).toEqual([])
  })

  it('each fixed one still gates content on the same flag it announces', () => {
    const gated: [string, string][] = [
      ['ChatPage.tsx', 'open'],
      ['prompts/SyntaxReference.tsx', 'open'],
      ['schedule/ScheduleDetail.tsx', 'expanded'],
    ]
    for (const [rel, flag] of gated) {
      const src = read(rel)
      expect(src, `${rel}: the toggle must announce ${flag}`).toMatch(new RegExp(`aria-expanded=\\{${flag}\\}`))
      expect(src, `${rel}: and ${flag} must be what reveals the content`).toMatch(new RegExp(`\\{${flag} && [(<]`))
    }
  })
})


describe('the disclosures whose toggle arrives as a PROP', () => {
  const PROP_DISCLOSURES: [string, string, string][] = [
    ['settings/ArchivePanel.tsx', 'open', 'onToggle={() => setOpen(open === a.name ? null : a.name)}'],
  ]

  for (const [rel, flag, toggle] of PROP_DISCLOSURES) {
    it(`${rel} announces expansion on the child that owns the button`, () => {
      const src = read(rel)
      expect(src, 'the parent must still hand the toggle down').toContain(toggle)
      expect(src, `the child's button must announce ${flag}`).toMatch(new RegExp(`aria-expanded=\\{${flag}\\}`))
      expect(src, `and ${flag} must be what reveals the content`).toMatch(new RegExp(`\\{${flag} && [(<]`))
    })
  }

  const PROP_TOGGLE = /\bon[A-Z]\w*=\{\(\) => \{?\s*set(\w+)\(([^)]*)\)/g

  it('the two older matchers are BLIND to this shape — the gap is structural', () => {
    const shape = "onToggle={() => setOpen(open === a.name ? null : a.name)}"
    expect(/onClick=\{\(\) => set\w+\(\(?\w*\)? ?=> ?!\w+\)|onClick=\{\(\) => set\w+\(!\w+\)/.test(shape),
      'the boolean-flip census cannot see it (no `!`, and not onClick=)').toBe(false)
    expect(/onClick=\{\(\) => set\w+\(\s*\w+ \? null : [\w.]+\s*\)/.test(shape),
      'the accordion census cannot see it either (onToggle=, and a `===` condition)').toBe(false)
    expect(PROP_TOGGLE.test(shape), 'and this one does').toBe(true)
    PROP_TOGGLE.lastIndex = 0
  })

  const walkPages = (d: string): string[] =>
    readdirSync(d).flatMap((n) => {
      const p = join(d, n)
      if (statSync(p).isDirectory()) return walkPages(p)
      return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
    })

  function propToggles() {
    const out: { rel: string; state: string; announced: boolean }[] = []
    for (const abs of walkPages(PAGES)) {
      const src = readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      for (const m of src.matchAll(PROP_TOGGLE)) {
        const arg = m[2]
        if (!/!\w+/.test(arg) && !/\?\s*null\s*:/.test(arg) && !/\?\s*''\s*:/.test(arg)) continue
        out.push({ rel: abs.slice(PAGES.length + 1), state: m[1], announced: announcesState(elementAt(src, m.index!)) || delegatedAnnouncement(src, m.index!, m[0], abs) })
      }
    }
    return out
  }

  it('finds the population (not vacuously green)', () => {
    const found = propToggles()
    expect(found.length, 'the prop-toggle scan must find its population').toBeGreaterThanOrEqual(23)
    expect(found.map((t) => t.rel), 'including the one this cycle drove').toContain('settings/ArchivePanel.tsx')
  })

  it('the silent remainder is a measured ceiling, and may only fall', () => {
    const silent = propToggles().filter((t) => !t.announced)
    expect(silent.length, `classify a new prop toggle, do not add to this number:\n${
      silent.map((s) => `${s.rel} set${s.state}`).join('\n')}`).toBeLessThanOrEqual(9)
  })
})
