import { createRef, useRef, useState } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EditorSelection, EditorState, Transaction } from '@codemirror/state'
import { EditorView } from '@codemirror/view'
import { markdown } from '@codemirror/lang-markdown'
import { ensureSyntaxTree } from '@codemirror/language'
import { MarkdownInput, type MarkdownInputHandle } from './MarkdownInput'
import { activeMention, activeSlash, EditorJournal, PromptRecall } from './editorState'
import { markdownDecorations } from './liveMarkdown'
import { ComposerSearchCache, formatMentionSize, mentionSearchKey } from './mentionSearch'
import { filterSlashCommands } from './SlashMenu'
import { composerPopupPlacement, useComposerTypeahead } from './composerTypeahead'

describe('editor trigger and history models', () => {
  it.each([
    ['@file', 5, { at: 0, query: 'file' }], ['hello @file', 11, { at: 6, query: 'file' }],
    ['hi\n@name rest', 8, { at: 3, query: 'name' }], ['name@host', 9, null], ['@@name', 6, null],
    ['\\@name', 6, null], ['@two words', 10, null], ['', 0, null],
  ])('finds only the current mention in %s', (text, caret, expected) => {
    expect(activeMention(text as string, caret as number)).toEqual(expected)
  })
  it('only treats a complete leading slash token as a command', () => {
    expect(activeSlash('/HeLP', 2)).toEqual({ query: 'HeLP' })
    for (const value of [' /help', '/help args', 'hi /help', '/help2']) expect(activeSlash(value, value.length)).toBeNull()
    expect(activeSlash('/help', 0)).toBeNull()
  })
  it('walks prompt history and restores the unsent draft at its boundary', () => {
    const recall = new PromptRecall()
    const messages = ['first', 'second', 'third']
    expect(recall.move('up', messages, 'unfinished', 0, 0)).toBeUndefined()
    expect(recall.move('up', messages, '', 0, 0)).toBe('third')
    expect(recall.move('up', messages, 'third', 5, 5)).toBe('second')
    expect(recall.move('down', messages, 'second', 0, 0)).toBeUndefined()
    expect(recall.move('down', messages, 'second', 6, 6)).toBe('third')
    expect(recall.move('down', messages, 'third', 5, 5)).toBe('')
    expect(recall.move('down', messages, '', 0, 0)).toBeUndefined()
  })
  it('groups adjacent typing, restores selections and drops redo after a new edit', () => {
    const journal = new EditorJournal()
    const before = { text: 'ab', anchor: 1, head: 1 }
    const middle = { text: 'axb', anchor: 2, head: 2 }
    const after = { text: 'axyb', anchor: 3, head: 3 }
    journal.record(before, middle, 'input.type', 100)
    journal.record(middle, after, 'input.type', 200)
    expect(journal.undo()).toEqual(before)
    expect(journal.redo()).toEqual(after)
    expect(journal.undo()).toEqual(before)
    journal.record(before, { text: 'new', anchor: 0, head: 3 }, 'input.paste', 300)
    expect(journal.redo()).toBeUndefined()
    expect(journal.undo()).toEqual(before)
  })
})

describe('markdown decoration rendering', () => {
  function inspect(text: string, selections: number[] = [0]) {
    const state = EditorState.create({ doc: text, selection: EditorSelection.create(selections.map(position => EditorSelection.cursor(position))),
      extensions: [markdown(), EditorState.allowMultipleSelections.of(true)] })
    expect(ensureSyntaxTree(state, text.length, 1000), 'the complete fixture must be parsed').not.toBeNull()
    const ranges = markdownDecorations(state, [{ from: 0, to: text.length }, { from: 0, to: text.length }])
    const found: { from: number; to: number; className?: string; widget?: unknown }[] = []
    const cursor = ranges.iter()
    while (cursor.value) {
      found.push({ from: cursor.from, to: cursor.to, className: cursor.value.spec.class, widget: cursor.value.spec.widget })
      cursor.next()
    }
    return { state, found }
  }
  it('styles active source while hiding markers only outside selected lines', () => {
    const text = '# Title\n**bold**\n- item\n1. ordered'
    const { state, found } = inspect(text)
    expect(state.doc.toString()).toBe(text)
    expect(found.some(item => item.className === 'cm-gideon-heading-1')).toBe(true)
    expect(found.some(item => item.from === 0 && !item.className)).toBe(false)
    expect(found.some(item => item.from === text.indexOf('**bold**') && !item.className)).toBe(true)
    expect(found.some(item => item.from === text.indexOf('- item') && item.widget)).toBe(true)
    expect(found.some(item => item.from === text.indexOf('1. ordered') && !item.className)).toBe(false)
    expect(new Set(found.map(item => `${item.from}:${item.to}:${item.className ?? 'replace'}`)).size).toBe(found.length)
  })
  it('keeps every selected line raw, including multiple cursors', () => {
    const text = '# Header\n**bold**\n*emphasis*'
    const { found } = inspect(text, [0, text.indexOf('bold')])
    expect(found.filter(item => !item.className && item.from < text.indexOf('*emphasis*'))).toEqual([])
    expect(found.some(item => item.from === text.indexOf('*emphasis*') && !item.className)).toBe(true)
  })
  it('does not interpret escaped markers or mutate literal HTML', () => {
    const text = 'editing\n\\*literal\\*\n<script>alert(1)</script>'
    const { found, state } = inspect(text)
    expect(found.some(item => item.className === 'cm-gideon-emphasis')).toBe(false)
    expect(state.doc.toString()).toBe(text)
  })
})

describe('the real CodeMirror editor', () => {
  function mount(initial: string, options: Partial<React.ComponentProps<typeof MarkdownInput>> = {}) {
    const handle = createRef<MarkdownInputHandle>()
    const changes: string[] = []
    function Controlled() {
      const [value, setValue] = useState(initial)
      return <MarkdownInput ref={handle} value={value} onChange={next => { changes.push(next); setValue(next) }}
        onSend={() => {}} canSend maxHeight={220} minHeight={36} {...options} />
    }
    const rendered = render(<Controlled />)
    const textbox = screen.getByRole('textbox', { name: 'Message input' })
    const view = EditorView.findFromDOM(textbox)!
    expect(view).not.toBeNull()
    return { ...rendered, handle, changes, textbox, view }
  }
  it('replaces the selected span through the public handle and can undo/redo it', () => {
    const editor = mount('hello world')
    act(() => editor.view.dispatch({ selection: { anchor: 6, head: 11 } }))
    act(() => editor.handle.current!.insertAtCaret('Gideon'))
    expect(editor.view.state.doc.toString()).toBe('hello Gideon')
    expect(editor.changes.at(-1)).toBe('hello Gideon')
    fireEvent.keyDown(editor.textbox, { key: 'z', ctrlKey: true })
    expect(editor.view.state.doc.toString()).toBe('hello world')
    expect(editor.view.state.selection.main.from).toBe(6)
    expect(editor.view.state.selection.main.to).toBe(11)
    fireEvent.keyDown(editor.textbox, { key: 'z', ctrlKey: true, shiftKey: true })
    expect(editor.view.state.doc.toString()).toBe('hello Gideon')
    editor.unmount()
  })
  it('applies Enter preference, prompt recall and IME ownership to actual key events', () => {
    let sends = 0
    const editor = mount('', { sendOnEnter: false, history: ['first', 'second'], onSend: () => { sends++ } })
    fireEvent.keyDown(editor.textbox, { key: 'ArrowUp' })
    expect(editor.view.state.doc.toString()).toBe('second')
    fireEvent.keyDown(editor.textbox, { key: 'ArrowUp' })
    expect(editor.view.state.doc.toString()).toBe('first')
    fireEvent.keyDown(editor.textbox, { key: 'ArrowDown' })
    expect(editor.view.state.doc.toString()).toBe('second')
    fireEvent.keyDown(editor.textbox, { key: 'ArrowDown' })
    expect(editor.view.state.doc.toString()).toBe('')
    fireEvent.keyDown(editor.textbox, { key: 'Enter' })
    expect(editor.view.state.doc.toString()).toBe('\n')
    fireEvent.compositionStart(editor.textbox)
    fireEvent.keyDown(editor.textbox, { key: 'Enter', isComposing: true })
    expect(editor.view.state.doc.toString()).toBe('\n')
    expect(sends).toBe(0)
    editor.unmount()
  })
  it('uses current callbacks and dynamically reconfigures placeholder and document', () => {
    let sends = 0
    const properties = { value: 'hello', onChange: (_value: string) => {}, canSend: true, maxHeight: 220, minHeight: 36 }
    const rendered = render(<MarkdownInput {...properties} onSend={() => { sends++ }} placeholder="First" />)
    const textbox = screen.getByRole('textbox')
    const view = EditorView.findFromDOM(textbox)!
    fireEvent.keyDown(textbox, { key: 'Enter' })
    expect(sends).toBe(1)
    rendered.rerender(<MarkdownInput {...properties} value="" onSend={() => { sends += 10 }} placeholder="Second" />)
    expect(view.state.doc.toString()).toBe('')
    expect(rendered.container.textContent).toContain('Second')
    fireEvent.keyDown(textbox, { key: 'Enter' })
    expect(sends).toBe(11)
    rendered.unmount()
  })
  it('renders HTML source as text, keeps typed edits, and retains the supplied height bounds', () => {
    const editor = mount('<img src=x onerror=alert(1)>')
    expect(editor.container.querySelector('img')).toBeNull()
    expect(editor.textbox.textContent).toContain('<img src=x onerror=alert(1)>')
    expect(editor.container.querySelector('[style*="min-height"]')?.getAttribute('style')).toContain('max-height: 220px')
    act(() => editor.view.dispatch({ changes: { from: editor.view.state.doc.length, insert: '!' }, annotations: Transaction.userEvent.of('input.type') }))
    expect(editor.changes.at(-1)).toContain('>!')
    editor.unmount()
  })
})

describe('typeahead selection and search models', () => {
  it('keeps caches scoped by project, case-insensitive query and leading-only prompt eligibility', async () => {
    expect(mentionSearchKey('AB', 'project', true)).toBe(mentionSearchKey('ab', 'project', true))
    expect(mentionSearchKey('ab', 'project', true)).not.toBe(mentionSearchKey('ab', 'project', false))
    const cache = new ComposerSearchCache<string[]>(30000, 2)
    const values = ['first']
    await cache.load('a', async () => values)
    expect(cache.peek('a')).toBe(values)
    await cache.load('b', async () => ['second'])
    await cache.load('c', async () => ['third'])
    expect(cache.peek('a')).toBeUndefined()
    expect(cache.peek('b', Infinity)).toBeUndefined()
  })
  it('filters commands by prefix or description and formats file size boundaries', () => {
    const commands = [{ name: '/help', description: 'List commands' }, { name: '/clear', description: 'New conversation' }]
    expect(filterSlashCommands(commands, 'HE')).toEqual([commands[0]])
    expect(filterSlashCommands(commands, 'conversation')).toEqual([commands[1]])
    expect([0, 1023, 1024, 1048576].map(formatMentionSize)).toEqual(['0B', '1023B', '1KB', '1.0MB'])
  })
  it('places menus within a narrow viewport and switches to below when the top has no room', () => {
    expect(composerPopupPlacement({ width: 500, left: 100, top: 30, bottom: 80 }, { width: 320, height: 600 }, 300, 140))
      .toEqual({ width: 304, left: 8, top: 88, maxHeight: 300 })
    expect(composerPopupPlacement({ width: 300, left: 10, top: 400, bottom: 440 }, { width: 800, height: 600 }, 300, 140))
      .toEqual({ width: 300, left: 10, bottom: 208, maxHeight: 300 })
  })
  it('clamps cursor reports after filtering and leaves composition keys to the editor', () => {
    const reports: (number | null)[] = []
    const picks: number[] = []
    function Menu({ count, identity }: { count: number; identity: string }) {
      const anchorRef = useRef<HTMLInputElement>(null)
      const menu = useComposerTypeahead({ open: true, anchorRef, count, identity, idPrefix: 'real-menu', height: 300, above: 140,
        onSelect: index => picks.push(index), onClose: () => {}, onActiveIndex: index => reports.push(index) })
      return <><input ref={anchorRef} aria-label="Draft" /><div ref={menu.menuRef} role="listbox" aria-label="Results">
        {Array.from({ length: count }, (_, index) => <button key={index} id={`real-menu-opt-${index}`} role="option" aria-selected={menu.cursor === index}>{index}</button>)}
      </div></>
    }
    const rendered = render(<Menu count={3} identity="all" />)
    const input = screen.getByRole('textbox', { name: 'Draft' })
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(reports.at(-1)).toBe(2)
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(picks).toEqual([])
    rendered.rerender(<Menu count={1} identity="filtered" />)
    expect(reports.at(-1)).toBe(0)
    fireEvent.keyDown(input, { key: 'Tab' })
    expect(picks).toEqual([0])
    rendered.unmount()
  })
})
