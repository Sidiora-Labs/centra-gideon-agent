import { forwardRef, useCallback, useEffect, useId, useImperativeHandle, useRef, useState } from 'react'
import { Annotation, Compartment, EditorState, Prec, Transaction } from '@codemirror/state'
import { EditorView, keymap, placeholder as cmPlaceholder } from '@codemirror/view'
import type { Unstable_TriggerPopoverAriaProps } from '@assistant-ui/react'
import { markdown } from '@codemirror/lang-markdown'
import { liveMarkdown, liveMarkdownTheme } from './liveMarkdown'
import { MentionMenu, type MentionPick } from './MentionMenu'
import { SlashMenu } from './SlashMenu'
import { enterKeyAction } from './enterKeyAction'
import { activeMention, activeSlash, EditorJournal, PromptRecall, type EditorSnapshot, type MentionTrigger, type TypeaheadCursor, typeaheadAttributes, updateTypeaheadCursor } from './editorState'

export interface MarkdownInputHandle { focus: () => void; insertAtCaret: (text: string) => void; applyDonorText: (text: string, caret: number) => void }
interface Props {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  canSend: boolean
  placeholder?: string
  maxHeight: number
  minHeight: number
  onFocusChange?: (focused: boolean) => void
  onOptimize?: () => void
  history?: string[]
  onMentionFile?: (file: { path: string; name: string }) => void
  onMentionKnowledge?: (item: { id: string; name: string }) => void
  mentionProject?: string
  slashCommands?: boolean
  onLargePaste?: (text: string) => boolean
  mobile?: boolean
  sendOnEnter?: boolean
  donorTriggers?: boolean
  onCursorChange?: (position: number) => void
  onTriggerKeyDown?: (event: KeyboardEvent) => boolean
  donorAria?: Unstable_TriggerPopoverAriaProps
}
const origin = Annotation.define<'recall' | 'journal' | 'host'>()
function snapshot(state: EditorState): EditorSnapshot {
  const { anchor, head } = state.selection.main
  return { text: state.doc.toString(), anchor, head }
}
function replaceDocument(view: EditorView, value: EditorSnapshot, source?: 'recall' | 'journal' | 'host') {
  view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: value.text },
    selection: { anchor: value.anchor, head: value.head }, annotations: source ? origin.of(source) : undefined })
}

export const MarkdownInput = forwardRef<MarkdownInputHandle, Props>(function MarkdownInput(props, ref) {
  const { value, placeholder, minHeight, maxHeight, onMentionFile, onMentionKnowledge, mentionProject, slashCommands } = props
  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<EditorView | null>(null)
  const cb = useRef(props)
  cb.current = props
  const compartments = useRef({ placeholder: new Compartment(), typeahead: new Compartment() })
  const comboId = useId().replace(/:/g, '')
  const recall = useRef(new PromptRecall())
  const journal = useRef(new EditorJournal())
  const composing = useRef(false)
  const [mention, setMention] = useState<MentionTrigger | null>(null)
  const [slash, setSlash] = useState<{ query: string } | null>(null)
  const triggers = useRef({ mention, slash })
  triggers.current = { mention, slash }
  const dismissed = useRef({ mention: '', slash: '' })
  const [cursor, setCursor] = useState<TypeaheadCursor | null>(null)
  const reportCursor = useCallback((list: TypeaheadCursor['list'], index: number | null) => {
    setCursor(previous => updateTypeaheadCursor(previous, list, index))
  }, [])

  const syncTriggers = useRef((view: EditorView) => {
    const text = view.state.doc.toString()
    const caret = view.state.selection.main.head
    const mention = !cb.current.donorTriggers && (cb.current.onMentionFile || cb.current.onMentionKnowledge) ? activeMention(text, caret) : null
    const slash = !cb.current.donorTriggers && cb.current.slashCommands ? activeSlash(text, caret) : null
    cb.current.onCursorChange?.(caret)
    const mentionKey = mention ? `${mention.at}:${mention.query}` : ''
    const slashKey = slash ? `/${slash.query}` : ''
    if (dismissed.current.mention !== mentionKey) dismissed.current.mention = ''
    if (dismissed.current.slash !== slashKey) dismissed.current.slash = ''
    const nextMention = mention && dismissed.current.mention !== mentionKey ? mention : null
    const nextSlash = slash && dismissed.current.slash !== slashKey ? slash : null
    triggers.current = { mention: nextMention, slash: nextSlash }
    setMention(previous => previous?.at === nextMention?.at && previous?.query === nextMention?.query ? previous : nextMention)
    setSlash(previous => previous?.query === nextSlash?.query ? previous : nextSlash)
  })

  useEffect(() => {
    if (!host.current) return
    const restore = (view: EditorView, direction: 'undo' | 'redo') => {
      if (composing.current || view.composing) return false
      const state = journal.current[direction]()
      if (!state) return false
      replaceDocument(view, state, 'journal')
      return true
    }
    const navigate = (view: EditorView, direction: 'up' | 'down') => {
      if (composing.current || view.composing || triggers.current.mention || triggers.current.slash) return false
      const current = snapshot(view.state)
      const text = recall.current.move(direction, cb.current.history ?? [], current.text, current.anchor, current.head)
      if (text === undefined) return false
      replaceDocument(view, { text, anchor: text.length, head: text.length }, 'recall')
      return true
    }
    const view = new EditorView({ parent: host.current, state: EditorState.create({
      doc: cb.current.value,
      extensions: [
        markdown(), liveMarkdown, liveMarkdownTheme, EditorView.lineWrapping,
        EditorView.contentAttributes.of({ 'aria-label': 'Message input' }),
        compartments.current.typeahead.of([]), compartments.current.placeholder.of(cmPlaceholder(cb.current.placeholder ?? '')),
        keymap.of([
          { key: 'Enter', run: current => {
            if (composing.current || current.composing) return false
            const action = enterKeyAction({ menuOpen: !!(triggers.current.mention || triggers.current.slash),
              mobile: cb.current.mobile, sendOnEnter: cb.current.sendOnEnter, canSend: cb.current.canSend })
            if (action === 'newline') { current.dispatch(current.state.replaceSelection('\n')); return true }
            if (action !== 'send') return false
            recall.current.reset(); cb.current.onSend(); return true
          } },
          { key: 'Shift-Enter', run: current => {
            if (composing.current || current.composing) return false
            current.dispatch(current.state.replaceSelection('\n')); return true
          } },
          { key: 'Mod-Enter', run: current => {
            if (composing.current || current.composing || !cb.current.onOptimize || !cb.current.canSend) return false
            cb.current.onOptimize(); return true
          } },
          { key: 'ArrowUp', run: current => navigate(current, 'up') },
          { key: 'ArrowDown', run: current => navigate(current, 'down') },
          { key: 'Mod-z', run: current => restore(current, 'undo') },
          { key: 'Mod-Shift-z', run: current => restore(current, 'redo') },
          { key: 'Ctrl-y', run: current => restore(current, 'redo') },
        ]),
        EditorView.updateListener.of(update => {
          if (update.docChanged) {
            const source = update.transactions.map(transaction => transaction.annotation(origin))
            if (!source.includes('journal')) journal.current.record(snapshot(update.startState), snapshot(update.state),
              update.transactions.at(-1)?.annotation(Transaction.userEvent) ?? '')
            if (!source.includes('recall')) recall.current.reset()
            const next = update.state.doc.toString()
            if (next !== cb.current.value) cb.current.onChange(next)
          }
          if (update.docChanged || update.selectionSet) syncTriggers.current(update.view)
          if (update.focusChanged) cb.current.onFocusChange?.(update.view.hasFocus)
        }),
        Prec.high(EditorView.domEventHandlers({
          keydown: event => !composing.current && !editor.current?.composing && !!cb.current.onTriggerKeyDown?.(event),
          compositionstart: () => { composing.current = true; return false },
          compositionend: () => { composing.current = false; return false },
          paste: event => {
            const text = event.clipboardData?.getData('text/plain')
            if (!text || !cb.current.onLargePaste?.(text)) return false
            event.preventDefault(); return true
          },
        })),
      ],
    }) })
    editor.current = view
    syncTriggers.current(view)
    return () => { editor.current = null; view.destroy() }
  }, [])

  useEffect(() => {
    const view = editor.current
    if (view && view.state.doc.toString() !== value) replaceDocument(view, { text: value, anchor: value.length, head: value.length }, 'host')
  }, [value])
  useEffect(() => {
    editor.current?.dispatch({ effects: compartments.current.placeholder.reconfigure(cmPlaceholder(placeholder ?? '')) })
  }, [placeholder])
  useEffect(() => {
    const donor = props.donorAria
    const attributes = donor?.['aria-controls'] ? {
      role: 'combobox', 'aria-controls': donor['aria-controls'], 'aria-expanded': 'true',
      'aria-haspopup': 'listbox', ...(donor['aria-activedescendant'] ? { 'aria-activedescendant': donor['aria-activedescendant'] } : {}),
    } : typeaheadAttributes(comboId, cursor)
    editor.current?.dispatch({ effects: compartments.current.typeahead.reconfigure(EditorView.contentAttributes.of(attributes)) })
  }, [comboId, cursor, props.donorAria])
  useEffect(() => { if (editor.current) syncTriggers.current(editor.current) }, [onMentionFile, onMentionKnowledge, slashCommands])

  const focusSoon = (view: EditorView) => requestAnimationFrame(() => { if (editor.current === view) view.focus() })
  useImperativeHandle(ref, () => ({
    focus() { editor.current?.focus() },
    applyDonorText(text, caret) {
      const view = editor.current
      if (!view) return
      const position = Math.min(Math.max(caret, 0), text.length)
      replaceDocument(view, { text, anchor: position, head: position })
      focusSoon(view)
    },
    insertAtCaret(text) {
      const view = editor.current
      if (!view) return
      view.dispatch(view.state.replaceSelection(text)); focusSoon(view)
    },
  }), [])

  const pickMention = (pick: MentionPick) => {
    const view = editor.current
    const trigger = triggers.current.mention
    if (!view || !trigger) return
    const insert = `@${pick.name} `
    view.dispatch({ changes: { from: trigger.at, to: trigger.at + trigger.query.length + 1, insert },
      selection: { anchor: trigger.at + insert.length } })
    if (pick.kind === 'file') cb.current.onMentionFile?.({ path: pick.path, name: pick.name })
    if (pick.kind === 'knowledge') cb.current.onMentionKnowledge?.({ id: pick.id, name: pick.name })
    setMention(null); focusSoon(view)
  }
  const pickSlash = (command: string) => {
    const view = editor.current
    if (!view) return
    const text = `${command} `
    replaceDocument(view, { text, anchor: text.length, head: text.length })
    setSlash(null); focusSoon(view)
  }
  return <div className="relative w-full">
    <div ref={host} className="w-full overflow-y-auto overscroll-contain px-s pt-1" style={{ minHeight, maxHeight }} />
    {!props.donorTriggers && (onMentionFile || onMentionKnowledge) && <MentionMenu query={mention?.query ?? ''} anchorRef={host} open={!!mention}
      project={mentionProject} leading={mention?.at === 0} idPrefix={`${comboId}-mention`}
      onActiveIndex={index => reportCursor('mention', index)} onSelect={pickMention}
      onClose={() => {
        const active = triggers.current.mention
        if (active) dismissed.current.mention = `${active.at}:${active.query}`
        triggers.current.mention = null; setMention(null)
      }} />}
    {!props.donorTriggers && slashCommands && <SlashMenu query={slash?.query ?? ''} anchorRef={host} open={!!slash} idPrefix={`${comboId}-slash`}
      onActiveIndex={index => reportCursor('slash', index)} onSelect={pickSlash}
      onClose={() => {
        if (triggers.current.slash) dismissed.current.slash = `/${triggers.current.slash.query}`
        triggers.current.slash = null; setSlash(null)
      }} />}
  </div>
})
