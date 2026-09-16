import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ListControls, ResultAnnouncement } from './ListControls'


function activeExpr(src: string, line: string, depth = 3): string {
  let expr = line.match(/active:\s*([^,}]+)/)?.[1]?.trim() ?? ''
  const strings: string[] = []
  const mask = (t: string) => t.replace(/'[^']*'/g, (m) => { strings.push(m); return `@@${strings.length - 1}@@` })
  const unmask = (t: string) => t.replace(/@@(\d+)@@/g, (_, i) => strings[Number(i)])
  expr = mask(expr)
  for (let i = 0; i < depth; i++) {
    let grew = false
    for (const id of new Set(expr.match(/[A-Za-z_$][\w$]*/g) ?? [])) {
      if (/^(undefined|null|true|false)$/.test(id)) continue
      const rhs = mask(src.match(new RegExp(`^  const ${id}\\s*=\\s*(.+)$`, 'm'))?.[1] ?? '') || undefined
      const balanced = rhs !== undefined
        && [...rhs].filter((c) => '([{'.includes(c)).length === [...rhs].filter((c) => ')]}'.includes(c)).length
      if (!rhs || !balanced || rhs.includes(id) || /use[A-Z]\w*\(|=>/.test(rhs)) continue
      expr = expr.replace(new RegExp(`\\b${id}\\b`, 'g'), `(${rhs})`)
      grew = true
    }
    if (!grew) break
  }
  return unmask(expr)
}

const opts = { value: '', onChange: () => {}, options: [] }

describe('a filtered list announces its result count', () => {
  it('mounts the live region even when idle', () => {
    const { container } = render(<ListControls search={{ value: '', onChange: () => {} }} />)
    const region = container.querySelector('[role="status"][aria-live="polite"]')
    expect(region, 'the region must exist before a filter is typed').not.toBeNull()
    expect(region!.textContent).toBe('')
  })

  it('says nothing while no filter is active', () => {
    const { container } = render(
      <ListControls search={{ value: '', onChange: () => {} }}
        results={{ count: 26, noun: 'items', active: false }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('')
  })

  it('announces the count while filtering', () => {
    const { container } = render(
      <ListControls search={{ value: 'zfs', onChange: () => {} }}
        results={{ count: 25, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('25 items')
  })

  it('singularises a single result', () => {
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 1, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('1 item')
  })

  it('names the empty result rather than announcing "0"', () => {
    const { container } = render(
      <ListControls search={{ value: 'zzz', onChange: () => {} }}
        results={{ count: 0, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('No matching items')
  })

  it('is polite, not assertive — a result count is an update, not an interruption', () => {
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 3, noun: 'items', active: true }} />,
    )
    const region = container.querySelector('[role="status"]')!
    expect(region.getAttribute('aria-live')).toBe('polite')
  })

  it('keeps the announcement out of the visible bar', () => {
    const { container } = render(
      <ListControls search={{ value: 'x', onChange: () => {} }}
        results={{ count: 3, noun: 'items', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.className).toContain('sr-only')
  })

  it('still renders nothing at all when the bar has no controls', () => {
    const { container } = render(<ListControls />)
    expect(container.firstChild).toBeNull()
  })

  it('works on a filter-only bar (no search box)', () => {
    const { container } = render(
      <ListControls filter={{ ...opts, value: 'open' }}
        results={{ count: 4, noun: 'triggers', active: true }} />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('4 triggers')
  })

  it('`empty` overrides the zero sentence for nouns the default composes badly with', () => {
    const { container } = render(
      <ResultAnnouncement count={0} noun="matches" empty="No matches" active />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('No matches')
  })

  it('`singular` overrides the trailing-"s" strip where it mangles the noun', () => {
    const { container } = render(
      <ResultAnnouncement count={1} noun="matches" singular="match" active />,
    )
    expect(container.querySelector('[role="status"]')!.textContent).toBe('1 match')
  })
})


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

function code(abs: string): string {
  return readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

function controlsTags(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/<ListControls\b/g)) {
    let depth = 0
    for (let j = m.index!; j < src.length; j++) {
      const c = src[j]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) { out.push(src.slice(m.index!, j + 1)); break }
    }
  }
  return out
}

function consumers(): Array<{ rel: string; tag: string }> {
  return walk(SRC).flatMap((abs) =>
    controlsTags(code(abs)).map((tag) => ({ rel: abs.replace(SRC + '/', ''), tag })),
  )
}

describe('EVERY list bar passes a result count — the ratchet', () => {
  it('the census finds the population', () => {
    const all = consumers()
    expect(all.length, 'the scan must find the bars').toBeGreaterThanOrEqual(15)
    const silent = all.filter((c) => !/results=\{\{/.test(c.tag)).map((c) => c.rel)
    expect(silent, 'a bar that narrows a list without announcing it tells a screen reader nothing')
      .toEqual([])
  })

  it('`active` is derived on every one of them, never hardcoded', () => {
    for (const { rel, tag } of consumers()) {
      const line = tag.split('\n').find((l) => l.includes('results={{')) ?? tag
      expect(line, `${rel}: active must not be hardcoded — it would announce at idle`)
        .not.toMatch(/active:\s*true/)
      const expr = activeExpr(code(join(SRC, rel)), line)
      expect(expr, `${rel}: active must reference the query or a filter`).toMatch(/(!!|\w+\s*!==)/)
    }
  })

  it('never compares `active` to a filter default it does not use', () => {
    let checked = 0
    for (const { rel, tag } of consumers()) {
      const src = code(join(SRC, rel))
      const line = tag.split('\n').find((l) => l.includes('results={{')) ?? tag
      for (const cmp of activeExpr(src, line).matchAll(/(\w+)\s*!==\s*'([^']+)'/g)) {
        const [, name, literal] = cmp
        const dflt =
          src.match(new RegExp(`const \\[${name},[^\\]]*\\] = useState(?:<[^>]*>)?\\('([^']*)'\\)`))?.[1]
          ?? src.match(new RegExp(`const \\[${name},[^\\]]*\\] = useQueryParam\\([^,]+,[^,]+,\\s*'[^']*',\\s*'([^']*)'`))?.[1]
        if (dflt === undefined) continue
        checked++
        expect(literal, `${rel}: '${name}' defaults to '${dflt}', so comparing to '${literal}' is ` +
          'true (or false) at rest').toBe(dflt)
      }
    }
    expect(checked, 'the scan must actually resolve some defaults').toBeGreaterThanOrEqual(12)
  })

  it('the count comes from the list the body renders, not a second filter chain', () => {
    const pins: Array<[string, RegExp]> = [
      ['features/agents/AgentsListPage.tsx', /count: shownCount\b/],
      ['features/code/CodeSection.tsx', /count: shown\.length\b/],
      ['features/tools/ToolsPage.tsx', /count: shownTools\b/],
      ['features/skills/SkillsPage.tsx', /count: results\?\.length \?\? 0/],
      ['features/apps/AppsSection.tsx', /count: \(libResult \?\? \[\]\)\.length/],
      ['features/apps/AppsSection.tsx', /count: storeResult\.length/],
    ]
    for (const [rel, re] of pins) {
      expect(code(join(SRC, rel)), `${rel} must count its own rendered list`).toMatch(re)
    }
    const agents = code(join(SRC, 'features/agents/AgentsListPage.tsx'))
    expect(agents, 'the native rows read the hoisted array').toMatch(/\{shownNative\.map\(/)
    expect(agents, 'and nothing re-filters it inline').not.toMatch(/native\.agents\.filter\(/)
  })

  it('a bar that renders during its own skeleton waits before announcing', () => {
    const guarded: Array<[string, RegExp]> = [
      ['features/apps/AppsSection.tsx', /active: apps !== undefined && libNarrowed/],
      ['features/apps/AppsSection.tsx', /active: catalog !== undefined && storeNarrowed/],
      ['features/tools/ToolsPage.tsx', /active: groups !== null && filtered/],
      ['features/agents/AgentsListPage.tsx', /active: !!n && !\(loading && groups\.length === 0\)/],
      ['features/skills/SkillsPage.tsx', /active: !!q\.trim\(\) && !loading && results !== null/],
    ]
    for (const [rel, re] of guarded) expect(code(join(SRC, rel)), `${rel}`).toMatch(re)
    const codeSection = code(join(SRC, 'features/code/CodeSection.tsx'))
    expect(codeSection, 'this bar renders inside `!!projects?.length`, so it needs no guard')
      .toMatch(/active: !!needle \|\| filter !== 'all'/)
  })

  it('one definition of "narrowed" per view, shared with the empty state', () => {
    const apps = code(join(SRC, 'features/apps/AppsSection.tsx'))
    expect(apps).toMatch(/const libNarrowed = /)
    expect(apps).toMatch(/const storeNarrowed = /)
    expect(apps, 'the empty state reads the shared flag').toMatch(/!libNarrowed \? \(/)
    expect(apps, 'and so does the Clear-filters affordance').toMatch(/filtersActive=\{storeNarrowed\}/)
    expect(apps, 'no second copy of the store expression').not.toMatch(
      /!!n \|\| storeType !== 'all' \|\| storeEntity !== 'all' \|\| storeTag !== 'all'[\s\S]{0,40}filtersActive/,
    )
  })
})

describe('the hand-laid bars reach the same idiom', () => {
  const DIRECT: [string, string, RegExp][] = [
    ['features/tasks/TasksListPage.tsx', 'tasks', /active=\{query\.trim\(\)\.length > 0\}/],
    ['features/artifacts/ArtifactsSection.tsx', 'artifacts', /active=\{!!\(q\.trim\(\) \|\| kind \|\| src \|\| col\)\}/],
    ['features/files/FilesSection.tsx', 'matches', /active=\{showResults\}/],
    ['features/settings/ArchivePanel.tsx', 'archived sessions', /active=\{!!needle\}/],
    ['features/settings/DiagnosticsPanel.tsx', 'lines', /active=\{q !== '' \|\| minLevel !== 'DEBUG'\}/],
    ['features/settings/MemoryPanel.tsx', 'memories', /active=\{!!q\.trim\(\) \|\| kindFilter !== 'all'\}/],
    ['features/settings/MemoryPanel.tsx', 'events', /active=\{q !== ''\}/],
    ['features/settings/ModelsPanel.tsx', 'models', /active=\{!!query\.trim\(\)\}/],
    ['features/settings/LocalModelManager.tsx', 'models',
      /active=\{!!query\.trim\(\) && !searching && searchResults !== null\}/],
    ['features/settings/OllamaModelManager.tsx', 'models',
      /active=\{!!q\.trim\(\) && !searching && results !== null\}/],
    ['features/settings/SettingsHome.tsx', 'settings', /active=\{q !== ''\}/],
  ]

  for (const [rel, noun, active] of DIRECT) {
    it(`${rel} renders the extracted announcement, not a copy of it`, () => {
      const src = readFileSync(join(SRC, rel), 'utf8')
      expect(src, 'must import the shared piece from the canonical module').toMatch(
        /import \{ ResultAnnouncement \} from '(\.\.\/)+shared\/ui\/ListControls'/,
      )
      const tags = [...src.matchAll(/<ResultAnnouncement[\s\S]{0,260}?\/>/g)].map((m) => m[0])
      expect(tags.length, `${rel} must render it`).toBeGreaterThanOrEqual(1)
      const tag = tags.find((t) => t.includes(`noun="${noun}"`)) ?? ''
      expect(tag, `one of them must use the noun "${noun}"`).toContain(`noun="${noun}"`)
      expect(tag, 'active must be this surface\'s own definition of narrowed').toMatch(active)
      for (const t of tags) {
        expect(t, 'active must never be hardcoded — it would announce at idle').not.toMatch(/active=\{true\}/)
      }
      expect(src, 'a hand-rolled region here would be the drift this change removes')
        .not.toMatch(/role="status" aria-live="polite" className="sr-only"/)
    })
  }

  it('each count comes from the array its own body renders', () => {
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const COUNTS: Array<[string, RegExp]> = [
      ['features/settings/ArchivePanel.tsx', /count=\{shown\.length\}/],
      ['features/settings/DiagnosticsPanel.tsx', /count=\{visible\.length\}/],
      ['features/settings/ModelsPanel.tsx', /count=\{filtered\.length\}/],
      ['features/settings/LocalModelManager.tsx', /count=\{searchResults\?\.length \?\? 0\}/],
      ['features/settings/OllamaModelManager.tsx', /count=\{results\?\.length \?\? 0\}/],
      ['features/settings/SettingsHome.tsx', /count=\{Object\.values\(matches\)\.filter\(Boolean\)\.length\}/],
    ]
    for (const [rel, re] of COUNTS) {
      expect(strip(readFileSync(join(SRC, rel), 'utf8')), `${rel} must count its own rendered list`)
        .toMatch(re)
    }
    const mem = strip(readFileSync(join(SRC, 'features/settings/MemoryPanel.tsx'), 'utf8'))
    expect((mem.match(/count=\{shown\.length\}/g) ?? []).length, 'both MemoryPanel lists').toBe(2)
    for (const [rel] of COUNTS) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      const tags = [...src.matchAll(/<ResultAnnouncement[\s\S]{0,260}?\/>/g)].map((m) => m[0])
      expect(tags.length, `${rel} must render the announcement`).toBeGreaterThanOrEqual(1)
      for (const tag of tags) {
        expect(tag, `${rel} must not count the unfiltered list`)
          .not.toMatch(/count=\{(?:archives|entries|capable|SETTINGS_WIDGETS|items|events)\.length\}/)
      }
    }
  })

  it('EVERY search control in the TREE announces — or is exempt for a CHECKED reason', () => {
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const hasControl = (src: string) =>
      /<SearchField\b/.test(src) || /<TextInput[^>]*ariaLabel="(?:Search|Filter)/.test(src)

    const EXEMPT: Record<string, [string, RegExp]> = {
      'app/shell/CommandPalette.tsx': ['role=listbox with options', /role="listbox"[\s\S]*role="option"/],
      'shared/ui/FindBar.tsx': ['visible aria-live match counter', /aria-live="polite"/],
      'features/code/CodeCockpitPage.tsx': [
        'a real combobox: focus stays in the field and aria-activedescendant moves',
        /aria-activedescendant|ariaActiveDescendant/,
      ],
      'shared/ui/ListControls.tsx': ['this IS the primitive', /export function ResultAnnouncement\b/],
    }

    const withControl = walk(SRC)
      .map((abs) => ({ rel: abs.replace(SRC + '/', ''), src: strip(readFileSync(abs, 'utf8')) }))
      .filter(({ src }) => hasControl(src))
    expect(withControl.length, 'the census must find the search controls').toBeGreaterThanOrEqual(17)

    const silent = withControl
      .filter(({ rel, src }) =>
        !src.includes('<ResultAnnouncement') && !src.includes('<ListControls') && !EXEMPT[rel])
      .map((f) => f.rel)
    expect(silent, 'a control that narrows a list without announcing it tells a screen reader nothing')
      .toEqual([])

    for (const [rel, [why, proof]] of Object.entries(EXEMPT)) {
      const src = strip(readFileSync(join(SRC, rel), 'utf8'))
      expect(src, `${rel} is exempt for "${why}" — which must still hold`).toMatch(proof)
    }
    for (const rel of Object.keys(EXEMPT)) {
      expect(hasControl(strip(readFileSync(join(SRC, rel), 'utf8'))), `${rel}: stale exemption`).toBe(true)
    }
  })

  it('ChatPage announces all three of its searches, each on its own terms', () => {
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const src = strip(readFileSync(join(SRC, 'features/ChatPage.tsx'), 'utf8'))
    expect((src.match(/<ResultAnnouncement\b/g) ?? []).length, 'chat list + two pickers').toBe(3)
    expect(src, 'the chat list counts its own filtered array').toMatch(
      /count=\{filtered\.length\} noun="chats"\s*\n?\s*active=\{!!n \|\| origin !== 'manual'\}/,
    )
    const chatsTag = src.match(/<ResultAnnouncement[^>]*noun="chats"[\s\S]{0,160}?\/>/)?.[0] ?? ''
    expect(chatsTag, 'the chats announcement must be found').toContain('noun="chats"')
    expect(chatsTag, 'and not compare against a default this surface does not use')
      .not.toMatch(/origin !== 'all'/)
    expect(chatsTag, 'the archived toggle is not a narrowing of this list').not.toMatch(/showArchived/)
    expect(src, 'the artifact picker counts what the cap actually renders')
      .toMatch(/count=\{shown\.length\} noun="artifacts" active=\{!!n\}/)
    expect(src, 'the knowledge picker waits for its debounced fetch').toMatch(
      /count=\{res\?\.results\.length \?\? 0\} noun="knowledge items"\s*\n?\s*active=\{!!q\.trim\(\) && !loading && res !== null\}/,
    )
  })

  it('MemoryPanel announces its two LISTS and not its context-preview input', () => {
    const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const src = strip(readFileSync(join(SRC, 'features/settings/MemoryPanel.tsx'), 'utf8'))
    const controls = (src.match(/<SearchField\b/g) ?? []).length
      + (src.match(/<TextInput[^>]*ariaLabel="(?:Search|Filter)/g) ?? []).length
    expect(controls, 'two SEARCH-shaped controls — the explorer and the audit log').toBe(2)
    expect((src.match(/<ResultAnnouncement\b/g) ?? []).length, 'one per filter, no more').toBe(2)
    expect(src, 'the third field feeds a retrieval preview').toContain('api.memoryContextPreview(q)')
    expect(src, 'and is named as a query, which is what keeps it out of the census')
      .toContain('ariaLabel="Query to preview injected memory context"')
  })

  it('ListControls itself routes through the extracted component', () => {
    const src = readFileSync(join(SRC, 'shared/ui/ListControls.tsx'), 'utf8')
    expect(src).toMatch(/<ResultAnnouncement /)
    expect(src).toMatch(/export function ResultAnnouncement\b/)
    const body = src.slice(0, src.indexOf('export function ResultAnnouncement'))
    expect(body, 'the bar must not still hold an inline region').not.toMatch(/role="status" aria-live="polite"/)
  })

  it('the zero branch reads correctly for every noun in use', () => {
    for (const noun of ['tasks', 'artifacts', 'lines', 'items']) {
      expect(`No matching ${noun}`, `"${noun}" must not restate the copy`).not.toMatch(/matching match/)
    }
    const files = readFileSync(join(SRC, 'features/files/FilesSection.tsx'), 'utf8')
    const tag = files.match(/<ResultAnnouncement[\s\S]{0,300}?\/>/)?.[0] ?? ''
    expect(tag, 'files announces the visible noun').toContain('noun="matches"')
    expect(tag, 'with the zero sentence the screen shows').toContain('empty="No matches"')
    expect(tag, 'and the count-1 form the strip mangles').toContain('singular="match"')
  })

  it('scans real files (not vacuously green)', () => {
    expect(walk(SRC).length).toBeGreaterThan(200)
  })
})
