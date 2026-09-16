import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { ContentType } from './contentTypes'
import { copyText } from '../../../app/shell/clipboard'

export type ContentView = 'preview' | 'edit' | 'split'
export type DraftCache = Map<string, { draft: string; base: string; warned?: boolean }>
export interface DraftState { id: string; draft: string; base: string; view: ContentView; customDirty: boolean }
export function contentPermissions(type: ContentType, readOnly?: boolean, truncated?: boolean, save?: unknown) {
  const custom = !!type.edit?.render && !readOnly && !truncated
  const editable = !!type.edit && !readOnly && !truncated && (!!save || custom)
  return { custom, editable, draftEditable: editable && !custom, previewable: !!type.preview, splittable: editable && !custom && !!type.preview && !!type.edit?.split }
}
export function reconcileContentDraft(state: DraftState, id: string, content: string, previewable: boolean, cache?: DraftCache): DraftState {
  if (state.id !== id) return { id, draft: cache?.get(id)?.draft ?? content, base: content, view: previewable ? state.view : 'edit', customDirty: false }
  if (state.base === content) return state
  return { ...state, base: content, draft: state.draft === state.base ? content : state.draft }
}
type DraftAction = { type: 'draft'; value: string } | { type: 'view'; value: ContentView } | { type: 'custom'; value: boolean } | { type: 'reconcile'; id: string; content: string; previewable: boolean; cache?: DraftCache }
function reduceDraft(state: DraftState, action: DraftAction): DraftState {
  switch (action.type) {
    case 'draft': return { ...state, draft: action.value }
    case 'view': return { ...state, view: action.value }
    case 'custom': return state.customDirty === action.value ? state : { ...state, customDirty: action.value }
    case 'reconcile': return reconcileContentDraft(state, action.id, action.content, action.previewable, action.cache)
  }
}
interface DraftOptions {
  id: string; content: string; previewable: boolean; editable: boolean; initialView?: ContentView; cache?: DraftCache
  save?: (draft: string) => void | Promise<void>; confirm?: () => boolean | Promise<boolean>
  onDirty?: (dirty: boolean) => void; onDraft?: (draft: string, dirty: boolean) => void
}
export function useContentDraft(options: DraftOptions) {
  const { id, content, previewable, editable, initialView, cache, save: persist, confirm, onDirty, onDraft } = options
  const [state, dispatch] = useReducer(reduceDraft, undefined, () => ({ id, draft: cache?.get(id)?.draft ?? content, base: content, view: initialView ?? (previewable ? 'preview' : 'edit'), customDirty: false }))
  const [saving, setSaving] = useState(false)
  const lock = useRef(false)
  const dirty = editable && state.draft !== content
  useEffect(() => dispatch({ type: 'reconcile', id, content, previewable, cache }), [id, content, previewable, cache])
  useEffect(() => {
    if (state.id !== id) return
    onDirty?.(dirty || state.customDirty)
  }, [state.id, id, dirty, state.customDirty, onDirty])
  useEffect(() => {
    if (state.id !== id) return
    onDraft?.(state.draft, dirty)
    if (!cache) return
    if (dirty) cache.set(id, { draft: state.draft, base: content, warned: cache.get(id)?.warned })
    else cache.delete(id)
  }, [state.id, state.draft, id, content, dirty, cache, onDraft])
  const perform = async (action: () => void | Promise<void>) => {
    if (lock.current) return
    lock.current = true
    setSaving(true)
    try { await action() } finally { lock.current = false; setSaving(false) }
  }
  const setDraft = useCallback((value: string) => dispatch({ type: 'draft', value }), [])
  const setView = useCallback((value: ContentView) => dispatch({ type: 'view', value }), [])
  const setCustomDirty = useCallback((value: boolean) => dispatch({ type: 'custom', value }), [])
  return {
    ...state, dirty, anyDirty: dirty || state.customDirty, saving,
    setDraft, setView, setCustomDirty,
    save: () => dirty && persist ? perform(async () => { if (!confirm || await confirm()) await persist(state.draft) }) : Promise.resolve(),
    action: (run: (draft: string) => void | Promise<void>) => perform(() => run(state.draft)),
  }
}

export function useContentTools(draft: string) {
  const [wrap, setWrap] = useState(() => { try { return localStorage.getItem('editor-wrap') !== 'off' } catch { return true } })
  const [copied, setCopied] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  useEffect(() => { try { localStorage.setItem('editor-wrap', wrap ? 'on' : 'off') } catch {} }, [wrap])
  useEffect(() => () => clearTimeout(timer.current), [])
  useEffect(() => {
    if (!exportOpen) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.stopPropagation(); setExportOpen(false) } }
    document.addEventListener('keydown', close)
    return () => document.removeEventListener('keydown', close)
  }, [exportOpen])
  return { wrap, setWrap, copied, exportOpen, setExportOpen, copy: async () => {
    if (!await copyText(draft, 'the document')) return
    clearTimeout(timer.current)
    setCopied(true)
    timer.current = setTimeout(() => setCopied(false), 1500)
  } }
}

export interface ContentScrollEditor {
  getScrollHeight(): number; getLayoutInfo(): { height: number }; getScrollTop(): number; setScrollTop(value: number): void
  onDidScrollChange?(listener: () => void): { dispose(): void }
}
export function proportionalScroll(offset: number, from: number, to: number): number {
  return from > 0 && to > 0 ? Math.max(0, Math.min(1, offset / from)) * to : 0
}
export function useContentScroll() {
  const preview = useRef<HTMLDivElement>(null)
  const editor = useRef<ContentScrollEditor | null>(null)
  const subscription = useRef<{ dispose(): void } | undefined>(undefined)
  const latch = useRef<'editor' | 'preview' | null>(null)
  const frame = useRef<number | undefined>(undefined)
  useEffect(() => () => { subscription.current?.dispose(); if (frame.current !== undefined) cancelAnimationFrame(frame.current) }, [])
  const sync = (owner: 'editor' | 'preview') => {
    if (latch.current && latch.current !== owner) { latch.current = null; return }
    const text = editor.current, view = preview.current
    if (!text || !view) return
    const textRange = text.getScrollHeight() - text.getLayoutInfo().height
    const viewRange = view.scrollHeight - view.clientHeight
    if ((owner === 'editor' ? textRange : viewRange) <= 0) return
    latch.current = owner
    if (owner === 'editor') view.scrollTop = proportionalScroll(text.getScrollTop(), textRange, viewRange)
    else text.setScrollTop(proportionalScroll(view.scrollTop, viewRange, textRange))
    if (frame.current !== undefined) cancelAnimationFrame(frame.current)
    frame.current = requestAnimationFrame(() => { latch.current = null })
  }
  return { preview, fromPreview: () => sync('preview'), mount: (instance: ContentScrollEditor) => {
    subscription.current?.dispose()
    editor.current = instance
    subscription.current = instance.onDidScrollChange?.(() => sync('editor'))
  } }
}
