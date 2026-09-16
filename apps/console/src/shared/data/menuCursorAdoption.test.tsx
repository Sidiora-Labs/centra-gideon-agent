import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const codeOf = (rel: string) => read(rel).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })

describe('the declaration-implies-implementation census', () => {
  const declaring = new Map<string, string[]>()
  for (const abs of walk(SRC)) {
    const rel = abs.slice(SRC.length + 1)
    const roles = [...codeOf(rel).matchAll(/role="(menu|listbox)"/g)].map((m) => m[1])
    if (roles.length) declaring.set(rel, [...new Set(roles)])
  }
  const COMBOBOX = [
    'shared/ui/composer/SlashMenu.tsx', 'shared/ui/composer/MentionMenu.tsx', 'app/shell/CommandPalette.tsx',
    'features/code/CodeCockpitPage.tsx',
  ]
  const focusMoving = [...declaring.keys()].filter((rel) => !COMBOBOX.includes(rel))

  it('finds the popups — the scan is not vacuous', () => {
    expect(declaring.size, 'containers declaring menu/listbox').toBeGreaterThanOrEqual(5)
  })

  it('a combobox popup is EXEMPT from the hook but not from the pattern', () => {
    const DEFERRED = ['shared/ui/composer/SlashMenu.tsx', 'shared/ui/composer/MentionMenu.tsx']
    for (const rel of COMBOBOX) {
      if (DEFERRED.includes(rel)) {
        expect(codeOf(rel), `${rel} now wires activedescendant — remove it from DEFERRED`)
          .not.toMatch(/aria-activedescendant/)
        continue
      }
      const code = codeOf(rel)
      expect(code, `${rel}: a combobox owes aria-activedescendant`).toMatch(/aria-activedescendant|ariaActiveDescendant/)
      expect(code, `${rel}: its popup children must be options`).toMatch(/role="option"/)
      expect(code, `${rel}: an active option must say so`).toMatch(/aria-selected/)
    }
  })

  it('every declaring file wires the shared cursor', () => {
    const missing = focusMoving.filter((rel) => !read(rel).includes('useMenuCursor'))
    expect(missing, `these declare a keyboard-navigable role but implement no cursor:\n${missing.join('\n')}`).toEqual([])
  })

  it('the five adopters are the ones we expect', () => {
    expect(focusMoving.sort()).toEqual([
      'features/files/browse/FileTree.tsx',
      'shared/ui/HeaderActions.tsx',
      'shared/ui/ProjectPicker.tsx',
      'shared/ui/Segmented.tsx',
      'shared/ui/motion/ContextMenu.tsx',
    ])
  })

  it('all five route arrows through the one reducer, not five spellings', () => {
    for (const rel of focusMoving) {
      expect(codeOf(rel), `${rel} should call menuCursorKeydown`).toMatch(/menuCursorKeydown\(/)
    }
    for (const rel of focusMoving.filter((r) => r !== 'shared/ui/Segmented.tsx')) {
      expect(codeOf(rel), `${rel} must not hand-roll ArrowDown`).not.toMatch(/key === 'ArrowDown'/)
    }
    const seg = codeOf('shared/ui/Segmented.tsx')
    const at = seg.indexOf('function CollapsedOptions')
    expect(at, 'the collapsed option list moved — re-anchor this slice').toBeGreaterThan(0)
    expect(seg.slice(at), 'the popup half must use the reducer only').not.toMatch(/key === 'Arrow/)
    expect(seg.slice(0, at), "the tablist half keeps its own horizontal handler").toMatch(/ArrowRight' \|\| e\.key === 'ArrowDown'/)
  })

  it('a listbox carries a name; a menu need not — the role difference axe reported', () => {
    expect(codeOf('shared/ui/ProjectPicker.tsx')).toMatch(/role="listbox"[\s\S]{0,120}aria-label="Project"/)
    expect(codeOf('shared/ui/Segmented.tsx')).toMatch(/role="listbox" aria-orientation="vertical" aria-label=\{ariaLabel\}/)
  })

  it('the pick-one popups open on their SELECTED option, not the top', () => {
    for (const rel of ['shared/ui/ProjectPicker.tsx', 'shared/ui/HeaderActions.tsx', 'shared/ui/Segmented.tsx']) {
      expect(read(rel), `${rel} should seed the cursor from the value`).toMatch(/initialIndex:/)
    }
    for (const rel of ['shared/ui/motion/ContextMenu.tsx', 'features/files/browse/FileTree.tsx']) {
      expect(read(rel), `${rel} is an action menu — no initialIndex`).not.toMatch(/initialIndex:/)
    }
  })

  it('the typeahead menus keep focus in the composer — a distinction, not a gap', () => {
    for (const rel of COMBOBOX) {
      expect(codeOf(rel), `${rel} owns its own cursor`).toMatch(/key === 'ArrowDown'/)
      expect(codeOf(rel), `${rel} must not move focus onto an option`).not.toMatch(/useMenuCursor/)
    }
  })

  it('a header popup portals, or the page body paints over it', () => {
    expect(codeOf('shared/ui/Segmented.tsx'), 'the collapsed pill must portal its Popover').toMatch(/placement="bottom"[\s\S]{0,80}portal/)
    expect(codeOf('shared/ui/HeaderActions.tsx')).toMatch(/createPortal\(/)
  })

  it('only the hover-opening popup declines autoFocus', () => {
    expect(read('shared/ui/HeaderActions.tsx'), 'the mode pill opens on mouseenter').toMatch(/autoFocus: false/)
    const others = ['shared/ui/ProjectPicker.tsx', 'shared/ui/Segmented.tsx', 'shared/ui/motion/ContextMenu.tsx', 'features/files/browse/FileTree.tsx']
    for (const rel of others) expect(read(rel), `${rel} opens by intent — focus should follow`).not.toMatch(/autoFocus: false/)
  })
})

describe('HeaderModePill: the portaled menu a keyboard user could not reach', () => {
  beforeEach(() => vi.resetModules())

  const renderPill = async () => {
    const { HeaderModePill } = await import('../ui/HeaderActions')
    const onChange = vi.fn()
    const view = render(
      <HeaderModePill ariaLabel="Task mode" value="plan" onChange={onChange}
        options={[{ key: 'agent', label: 'Agent' }, { key: 'ask', label: 'Ask' }, { key: 'plan', label: 'Plan' }]} />,
    )
    return { view, onChange }
  }

  it('opening on hover does NOT move focus — the pointer must not be hijacked', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    expect(document.activeElement, 'focus stays on the trigger until a key is pressed').toBe(trigger)
  })

  it('the first ArrowDown pulls focus in WITHOUT skipping the checked option', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'ArrowDown' }) })
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Plan' }))
    act(() => { fireEvent.keyDown(window, { key: 'ArrowUp' }) })
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Ask' }))
    act(() => { fireEvent.keyDown(window, { key: 'Home' }) })
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Agent' }))
  })

  it('Home from OUTSIDE the list jumps to the first option — only arrows just-enter', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'Home' }) })
    expect(document.activeElement).toBe(screen.getByRole('menuitemradio', { name: 'Agent' }))
  })

  it('is one tab stop, not four', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    expect(screen.getAllByRole('menuitemradio').map((r) => r.getAttribute('tabindex'))).toEqual(['-1', '-1', '0'])
  })

  it('Tab dismisses instead of walking into the header behind it', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'Tab' }) })
    await waitFor(() => expect(screen.queryAllByRole('menuitemradio')).toHaveLength(0))
    expect(document.activeElement).toBe(trigger)
  })

  it('Escape closes it and the reopen latch does not stick', async () => {
    await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }) })
    await waitFor(() => expect(screen.queryAllByRole('menuitemradio')).toHaveLength(0))
    expect(document.activeElement).toBe(trigger)
    await new Promise((r) => setTimeout(r, 5))
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
  })

  it('choosing with the keyboard commits the value once', async () => {
    const { onChange } = await renderPill()
    const trigger = screen.getByRole('button', { name: /^Task mode:/ })
    trigger.focus()
    act(() => { fireEvent.mouseEnter(trigger.parentElement!) })
    await waitFor(() => expect(screen.getAllByRole('menuitemradio')).toHaveLength(3))
    act(() => { fireEvent.keyDown(window, { key: 'Home' }) })
    const cursor = document.activeElement as HTMLElement
    act(() => { fireEvent.keyDown(cursor, { key: 'Enter' }); fireEvent.click(cursor) })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange).toHaveBeenCalledWith('agent')
  })
})

describe('ProjectPicker: a listbox that promised arrows', () => {
  beforeEach(() => vi.resetModules())

  const renderPicker = async (value = '') => {
    vi.doMock('./api', async (orig) => {
      const real = await orig<typeof import('./api')>()
      return { ...real, api: { ...real.api, projects: async () => ([
        { id: 'p1', name: 'Personal', status: 'active' },
        { id: 'p2', name: 'Repeatable', status: 'active' },
      ]) } }
    })
    const { ProjectPicker } = await import('../ui/ProjectPicker')
    const onChange = vi.fn()
    render(<ProjectPicker value={value} onChange={onChange} emptyLabel="No project" />)
    const trigger = screen.getByRole('button', { name: /^Project/ })
    trigger.focus()
    act(() => { fireEvent.click(trigger) })
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1))
    return { trigger, onChange }
  }

  it('names its listbox — the axe serious finding', async () => {
    await renderPicker()
    expect(screen.getByRole('listbox', { name: 'Project' })).toBeTruthy()
  })

  it('moves focus onto the selected option when it opens', async () => {
    await renderPicker('p2')
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('option', { name: /Repeatable/ })))
  })

  it('arrows walk the options and the list is one tab stop', async () => {
    await renderPicker()
    const opts = screen.getAllByRole('option')
    await waitFor(() => expect(document.activeElement).toBe(opts[0]))
    expect(opts.map((o) => o.getAttribute('tabindex'))).toEqual(['0', '-1', '-1'])
    act(() => { fireEvent.keyDown(window, { key: 'End' }) })
    expect(document.activeElement).toBe(screen.getAllByRole('option')[2])
    expect(screen.getAllByRole('option').map((o) => o.getAttribute('tabindex'))).toEqual(['-1', '-1', '0'])
  })

  it('Escape closes it and hands focus back to the trigger', async () => {
    const { trigger } = await renderPicker()
    await waitFor(() => expect(document.activeElement).not.toBe(trigger))
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }) })
    await waitFor(() => expect(screen.queryAllByRole('option')).toHaveLength(0))
    expect(document.activeElement).toBe(trigger)
  })
})

describe('the quick-open combobox says what it is doing', () => {
  const code = codeOf('features/code/CodeCockpitPage.tsx')

  it('the field points at the list and tracks the active option', () => {
    expect(code).toMatch(/ariaHasPopup="listbox" ariaControls=\{`\$\{qoId\}-list`\}/)
    expect(code, 'the cursor is virtual, so the id must follow `hi`').toMatch(
      /ariaActiveDescendant=\{open && results\.length \? `\$\{qoId\}-opt-\$\{Math\.min\(hi, results\.length - 1\)\}` : undefined\}/,
    )
  })

  it('says whether the popover is open — the attribute CommandPalette does not need', () => {
    expect(code).toMatch(/ariaExpanded=\{open && q\.trim\(\)\.length >= 2\}/)
    expect(readFileSync(join(SRC, 'shared/ui/SearchField.tsx'), 'utf8'), 'and the field must pass it through')
      .toMatch(/'aria-expanded': ariaExpanded/)
  })

  it('the options are options, and the active one says so', () => {
    expect(code).toMatch(/role="option" aria-selected=\{i === hi\}/)
    expect(code, 'each option needs the id the field points at').toMatch(/id=\{`\$\{qoId\}-opt-\$\{i\}`\}/)
  })

  it('the listbox is NAMED — the axe serious finding a nameless one produces', () => {
    expect(code).toMatch(/role="listbox" aria-label="Matching files"/)
  })
})
