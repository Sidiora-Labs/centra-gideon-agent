import { useEffect, useReducer, useRef } from 'react'
import { api, ApiError, type DocumentModelJson, type DocumentLossReport } from '../../data/api'

export interface DocumentBaseline { model: DocumentModelJson; loss: DocumentLossReport; version: number }
export interface DocumentEditState {
  slug: string; baseline: DocumentBaseline | null; model: DocumentModelJson | null
  acknowledged: boolean; saving: boolean; loadError: string; saveError: string
}
export function emptyDocumentState(slug: string): DocumentEditState {
  return { slug, baseline: null, model: null, acknowledged: false, saving: false, loadError: '', saveError: '' }
}
export type DocumentEditAction =
  | { type: 'open'; slug: string }
  | { type: 'loaded'; baseline: DocumentBaseline }
  | { type: 'load-failed'; message: string }
  | { type: 'edit'; transform: (model: DocumentModelJson) => DocumentModelJson }
  | { type: 'acknowledge' }
  | { type: 'saving'; value: boolean }
  | { type: 'saved'; model: DocumentModelJson; version: number }
  | { type: 'save-error'; message: string }

export function documentEditReducer(state: DocumentEditState, action: DocumentEditAction): DocumentEditState {
  switch (action.type) {
    case 'open': return emptyDocumentState(action.slug)
    case 'loaded': return { ...state, baseline: action.baseline, model: action.baseline.model, acknowledged: action.baseline.loss.lossless, loadError: '' }
    case 'load-failed': return { ...state, loadError: action.message }
    case 'edit': return state.model ? { ...state, model: action.transform(state.model) } : state
    case 'acknowledge': return { ...state, acknowledged: true }
    case 'saving': return { ...state, saving: action.value }
    case 'saved': return state.baseline ? { ...state, baseline: { ...state.baseline, model: action.model, version: action.version } } : state
    case 'save-error': return { ...state, saveError: action.message }
  }
}
export function documentIsDirty(state: DocumentEditState): boolean {
  return !!state.model && !!state.baseline && state.model !== state.baseline.model
}
export function documentSaveError(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return 'This document changed somewhere else (another tab, or the agent) since you opened it. Your edits are still here — reopen the document to get the current version, then re-apply them.'
  return error instanceof Error ? error.message : String(error)
}

export function useDocumentEditorState(slug: string, readOnly?: boolean, onDirty?: (dirty: boolean) => void) {
  const [state, dispatch] = useReducer(documentEditReducer, slug, emptyDocumentState)
  const epoch = useRef(0)
  const lock = useRef(false)
  useEffect(() => {
    const revision = ++epoch.current
    lock.current = false
    dispatch({ type: 'open', slug })
    api.artifactModel(slug).then(result => {
      if (revision === epoch.current) dispatch({ type: 'loaded', baseline: { model: result.model, loss: result.loss, version: result.version } })
    }).catch(error => { if (revision === epoch.current) dispatch({ type: 'load-failed', message: error instanceof Error ? error.message : String(error) }) })
    return () => { epoch.current++ }
  }, [slug])
  const dirty = state.slug === slug && documentIsDirty(state)
  useEffect(() => onDirty?.(dirty), [dirty, onDirty])
  const editable = state.slug === slug && !readOnly && state.acknowledged
  const save = async (confirmLoss: (baseline: DocumentBaseline) => Promise<boolean>) => {
    if (!editable || !dirty || !state.model || !state.baseline || lock.current) return
    lock.current = true
    const revision = epoch.current
    const model = state.model
    const baseline = state.baseline
    dispatch({ type: 'saving', value: true })
    dispatch({ type: 'save-error', message: '' })
    try {
      if (!baseline.loss.lossless && !await confirmLoss(baseline)) return
      if (revision !== epoch.current) return
      const saved = await api.saveArtifactModel(slug, baseline.version, model)
      if (revision === epoch.current) dispatch({ type: 'saved', model, version: saved.version })
    } catch (error) {
      if (revision === epoch.current) dispatch({ type: 'save-error', message: documentSaveError(error) })
    } finally {
      if (revision === epoch.current) { lock.current = false; dispatch({ type: 'saving', value: false }) }
    }
  }
  return {
    ...state, dirty, editable, save,
    ready: state.slug === slug && !!state.model && !!state.baseline,
    acknowledge: () => dispatch({ type: 'acknowledge' }),
    clearSaveError: () => dispatch({ type: 'save-error', message: '' }),
    edit: (transform: (model: DocumentModelJson) => DocumentModelJson) => { if (editable) dispatch({ type: 'edit', transform }) },
  }
}
