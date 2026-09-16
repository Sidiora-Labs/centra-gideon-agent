import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { typeaheadAttributes, updateTypeaheadCursor } from './editorState'

vi.mock('../../data/api', async (orig) => {
  const real = await orig<typeof import('../../data/api')>()
  return { ...real, api: { ...real.api, slashCommands: async () => ([
    { name: '/help', description: 'List available commands' },
    { name: '/clear', description: 'Start a fresh thread' },
    { name: '/model', description: 'Switch the model' },
  ]) } }
})


if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {}

const COMPOSER = join(process.cwd(), "src/shared/ui/composer")
const read = (f: string) => readFileSync(join(COMPOSER, f), 'utf8')
const codeOf = (f: string) => read(f).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('SlashMenu publishes the cursor its editor has to announce', () => {
  const anchor = { current: document.createElement('div') } as React.RefObject<HTMLElement>
  let SlashMenu: typeof import('./SlashMenu')['SlashMenu']
  beforeEach(async () => { ({ SlashMenu } = await import('./SlashMenu')) })

  it('gives the listbox and every option an id from the shared prefix', async () => {
    render(<SlashMenu query="" anchorRef={anchor} open idPrefix="cmp-slash" onActiveIndex={vi.fn()}
      onSelect={vi.fn()} onClose={vi.fn()} />)
    const list = await screen.findByRole('listbox', { name: 'Slash commands' })
    expect(list.id).toBe('cmp-slash-list')
    const opts = screen.getAllByRole('option')
    expect(opts.length).toBeGreaterThan(0)
    expect(opts[0].id).toBe('cmp-slash-opt-0')
    for (const [i, o] of opts.entries()) expect(document.getElementById(`cmp-slash-opt-${i}`)).toBe(o)
  })

  it('reports the cursor index while open, and null when it closes', async () => {
    const onActiveIndex = vi.fn()
    const { rerender } = render(<SlashMenu query="" anchorRef={anchor} open idPrefix="p" onActiveIndex={onActiveIndex}
      onSelect={vi.fn()} onClose={vi.fn()} />)
    await screen.findByRole('listbox')
    expect(onActiveIndex).toHaveBeenCalledWith(0)
    act(() => { fireEvent.keyDown(document, { key: 'ArrowDown' }) })
    expect(onActiveIndex).toHaveBeenLastCalledWith(1)
    onActiveIndex.mockClear()
    rerender(<SlashMenu query="" anchorRef={anchor} open={false} idPrefix="p" onActiveIndex={onActiveIndex}
      onSelect={vi.fn()} onClose={vi.fn()} />)
    expect(onActiveIndex, 'closing must clear the editor attribute').toHaveBeenLastCalledWith(null)
  })

  it('keeps aria-selected on the cursor option — the visual half is unchanged', async () => {
    render(<SlashMenu query="" anchorRef={anchor} open idPrefix="q" onActiveIndex={vi.fn()}
      onSelect={vi.fn()} onClose={vi.fn()} />)
    await screen.findByRole('listbox')
    const opts = screen.getAllByRole('option')
    expect(opts.filter((o) => o.getAttribute('aria-selected') === 'true')).toHaveLength(1)
  })
})

describe('both typeahead menus, and the editor that speaks for them', () => {
  it('the cursor hook runs ABOVE the early return in both', () => {
    for (const file of ['SlashMenu.tsx', 'MentionMenu.tsx']) {
      const code = codeOf(file)
      const hook = code.indexOf('const menu = useComposerTypeahead(')
      const early = code.search(/if \(!open \|\| !anchorRef\.current/)
      expect(hook).toBeGreaterThan(0)
      expect(early).toBeGreaterThan(hook)
    }
    expect(codeOf('composerTypeahead.tsx')).toContain('onActiveIndex(open && count > 0 ? cursor : null)')
  })

  it('the mention listbox is named — it was the one axe could see', () => {
    expect(codeOf('MentionMenu.tsx')).toMatch(/aria-label=\{leading \? 'Prompts, files and knowledge' : 'Files and knowledge'\}/)
  })

  it('the editor points at the cursor and clears attributes when the menu closes', () => {
    expect(typeaheadAttributes('composer', { list: 'slash', index: 2 })).toEqual({
      'aria-activedescendant': 'composer-slash-opt-2', 'aria-controls': 'composer-slash-list', 'aria-haspopup': 'listbox',
    })
    expect(typeaheadAttributes('composer', null)).toEqual({})
    expect(codeOf('MarkdownInput.tsx')).toContain('EditorView.contentAttributes.of(typeaheadAttributes(comboId, cursor))')
    expect(codeOf('editorState.ts')).not.toContain("'aria-expanded'")
  })

  it('the attributes are compartment-swapped, not set on a rebuild', () => {
    const code = codeOf('MarkdownInput.tsx')
    expect(code).toContain('compartments.current.typeahead.reconfigure(')
    expect(code).toContain('compartments.current.typeahead.of([])')
  })

  it('the host dedupes cursor reports and only clears the owner that closed', () => {
    const current = { list: 'mention' as const, index: 3 }
    expect(updateTypeaheadCursor(current, 'mention', 3)).toBe(current)
    expect(updateTypeaheadCursor(current, 'slash', null)).toBe(current)
    expect(updateTypeaheadCursor(current, 'mention', null)).toBeNull()
    expect(codeOf('MarkdownInput.tsx')).toContain('updateTypeaheadCursor(previous, list, index)')
  })
})
