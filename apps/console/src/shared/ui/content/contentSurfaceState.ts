import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import type { ContentType } from './contentTypes'
import { copyText } from '../../../app/shell/clipboard'
import { mergeText } from '../../data/staleWrite'

export type ContentView = 'preview' | 'edit' | 'split'
export interface DraftCacheEntry { draft: string; base: string; revision?: string; validator?: string; warned?: boolean }
export type DraftCache = Map<string, DraftCacheEntry>
export interface DraftAuthority { content: string; revision?: string; validator?: string }
export interface DraftState { id: string; draft: string; base: string; baseRevision?: string; baseValidator?: string; view: ContentView; customDirty: boolean }
export function contentPermissions(type: ContentType, readOnly?: boolean, truncated?: boolean, save?: unknown) {
  const custom = !!type.edit?.render && !readOnly && !truncated
  const editable = !!type.edit && !readOnly && !truncated && (!!save || custom)
  return { custom, editable, draftEditable: editable && !custom, previewable: !!type.preview, splittable: editable && !custom && !!type.preview && !!type.edit?.split }
}
export function reconcileContentDraft(state: DraftState, id: string, content: string, previewable: boolean, cache?: DraftCache, revision?: string, validator?: string): DraftState {
  if (state.id !== id) {
    const cached = cache?.get(id)
    const preserve = cached && cached.draft !== cached.base && cached.draft !== content ? cached : undefined
    return {
      id,
      draft: preserve?.draft ?? content,
      base: preserve?.base ?? content,
      baseRevision: preserve ? preserve.revision : revision,
      baseValidator: preserve ? preserve.validator : validator,
      view: previewable ? state.view : 'edit',
      customDirty: false,
    }
  }
  if (state.draft === state.base || state.draft === content) {
    if (state.base === content && state.baseRevision === revision && state.baseValidator === validator) return state
    return { ...state, draft: content, base: content, baseRevision: revision, baseValidator: validator }
  }
  // A dirty draft keeps the revision pair it was displayed against. A newer read is
  // not permission to bind old text to a new revision; the server must refuse it or
  // the user must explicitly rebase it.
  return state
}
type DraftAction = { type: 'draft'; value: string } | { type: 'view'; value: ContentView } | { type: 'custom'; value: boolean } | { type: 'reconcile'; id: string; content: string; previewable: boolean; cache?: DraftCache; revision?: string; validator?: string } | { type: 'rebase'; content: string; draft: string; revision: string; validator?: string }
function reduceDraft(state: DraftState, action: DraftAction): DraftState {
  switch (action.type) {
    case 'draft': return { ...state, draft: action.value }
    case 'view': return { ...state, view: action.value }
    case 'custom': return state.customDirty === action.value ? state : { ...state, customDirty: action.value }
    case 'reconcile': return reconcileContentDraft(state, action.id, action.content, action.previewable, action.cache, action.revision, action.validator)
    case 'rebase': return { ...state, base: action.content, draft: action.draft, baseRevision: action.revision, baseValidator: action.validator }
  }
}
interface DraftOptions {
  id: string; content: string; previewable: boolean; editable: boolean; initialView?: ContentView; cache?: DraftCache; revision?: string; validator?: string; requireRevision?: boolean; requireValidator?: boolean
  save?: (draft: string, base: DraftAuthority) => void | Promise<void>; confirm?: () => boolean | Promise<boolean>
  readCurrent?: () => Promise<DraftAuthority>; onRebased?: (authority: DraftAuthority) => void
  onDirty?: (dirty: boolean) => void; onDraft?: (draft: string, dirty: boolean) => void
}
export function useContentDraft(options: DraftOptions) {
  const { id, content, previewable, editable, initialView, cache, revision, validator, requireRevision, requireValidator, save: persist, confirm, readCurrent, onRebased, onDirty, onDraft } = options
  const [state, dispatch] = useReducer(reduceDraft, undefined, () => {
    const cached = cache?.get(id)
    return { id, draft: cached?.draft ?? content, base: cached?.base ?? content, baseRevision: cached ? cached.revision : revision, baseValidator: cached ? cached.validator : validator, view: initialView ?? (previewable ? 'preview' : 'edit'), customDirty: false }
  })
  const [saving, setSaving] = useState(false)
  const [baseError, setBaseError] = useState('')
  const lock = useRef(false)
  const dirty = editable && state.draft !== state.base
  const baseMissing = editable && ((requireRevision && !state.baseRevision) || (requireValidator && !state.baseValidator))
  useEffect(() => dispatch({ type: 'reconcile', id, content, previewable, cache, revision, validator }), [id, content, previewable, cache, revision, validator])
  useEffect(() => {
    if (state.id !== id) return
    onDirty?.(dirty || state.customDirty)
  }, [state.id, id, dirty, state.customDirty, onDirty])
  useEffect(() => {
    if (state.id !== id) return
    onDraft?.(state.draft, dirty)
    if (!cache) return
    if (dirty) cache.set(id, { draft: state.draft, base: state.base, revision: state.baseRevision, validator: state.baseValidator, warned: cache.get(id)?.warned })
    else cache.delete(id)
  }, [state.id, state.draft, state.base, state.baseRevision, state.baseValidator, id, dirty, cache, onDraft])
  const perform = async (action: () => void | Promise<void>) => {
    if (lock.current) return
    lock.current = true
    setSaving(true)
    try { await action() } finally { lock.current = false; setSaving(false) }
  }
  const setDraft = useCallback((value: string) => dispatch({ type: 'draft', value }), [])
  const setView = useCallback((value: ContentView) => dispatch({ type: 'view', value }), [])
  const setCustomDirty = useCallback((value: boolean) => dispatch({ type: 'custom', value }), [])
  const rebaseMissing = async () => {
    if (!readCurrent) return
    setBaseError('')
    try {
      const current = await readCurrent()
      if (!current.revision || (requireValidator && !current.validator)) throw new Error('The current version is incomplete. Reload it before rebasing.')
      const merged = mergeText(state.base, state.draft, current.content)
      if (merged === null) throw new Error('The edits overlap. Keep this draft and resolve the difference before rebasing.')
      dispatch({ type: 'rebase', content: current.content, draft: merged, revision: current.revision, validator: current.validator })
      onRebased?.(current)
    } catch (error) {
      setBaseError(error instanceof Error ? error.message : 'Could not rebase this draft.')
    }
  }
  return {
    ...state, dirty, anyDirty: dirty || state.customDirty, saving, baseMissing, baseError,
    setDraft, setView, setCustomDirty,
    rebaseMissing,
    save: () => dirty && persist && !baseMissing ? perform(async () => { if (!confirm || await confirm()) await persist(state.draft, { content: state.base, revision: state.baseRevision, validator: state.baseValidator }) }) : Promise.resolve(),
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
