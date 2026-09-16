import { useEffect, useSyncExternalStore, type ComponentType } from 'react'
import { api } from '../../data/api'
import { getContentType, registerContentType, type DocumentEditorProps } from './contentTypes'
import { DocumentEditor } from './DocumentEditor'
import { SheetGrid } from './SheetGrid'
import { SlideDeck } from './SlideDeck'

export const DOCUMENT_EDITING_TYPE_IDS = ['docx', 'xlsx', 'pptx'] as const
const editors: Record<(typeof DOCUMENT_EDITING_TYPE_IDS)[number], ComponentType<DocumentEditorProps>> = { docx: DocumentEditor, xlsx: SheetGrid, pptx: SlideDeck }
const listeners = new Set<() => void>()
let enabled = false
let request: Promise<boolean> | undefined
let generation = 0

export function setDocumentEditing(on: boolean): void {
  let changed = enabled !== on
  for (const id of DOCUMENT_EDITING_TYPE_IDS) {
    const entry = getContentType(id)
    if (!entry || Boolean(entry.edit) === on) continue
    const next = { ...entry }
    if (on) next.edit = { language: 'plaintext', render: editors[id] }
    else delete next.edit
    registerContentType(next)
    changed = true
  }
  enabled = on
  if (changed) for (const notify of listeners) notify()
}
export function documentEditingApplied(): boolean { return enabled }
export function loadDocumentEditing(): Promise<boolean> {
  if (request) return request
  const revision = generation
  request = api.dashboardConfig().then(config => {
    if (revision === generation) setDocumentEditing(Boolean(config.document_editing))
    return enabled
  }).catch(() => enabled)
  return request
}
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
export function useDocumentEditing(): boolean {
  const active = useSyncExternalStore(subscribe, documentEditingApplied, documentEditingApplied)
  useEffect(() => { void loadDocumentEditing() }, [])
  return active
}
export function resetDocumentEditingForTests(): void {
  generation++
  request = undefined
  setDocumentEditing(false)
}
