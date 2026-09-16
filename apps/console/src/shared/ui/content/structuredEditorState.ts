import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { ApiError, type DocumentLossReport } from '../../data/api'

export interface StructuredBaseline<Model> { model: Model; loss: DocumentLossReport; version: number }
export interface StructuredSnapshot<Model> {
  slug: string; baseline: StructuredBaseline<Model> | null; model: Model | null
  acknowledged: boolean; saving: boolean; loadError: string; saveError: string
}
export interface StructuredSave<Model> { generation: number; baseline: StructuredBaseline<Model>; model: Model }
const initialSnapshot = <Model,>(slug: string): StructuredSnapshot<Model> => ({ slug, baseline: null, model: null, acknowledged: false, saving: false, loadError: '', saveError: '' })

export class StructuredEditorWorkspace<Model> {
  private state: StructuredSnapshot<Model> = initialSnapshot('')
  private generation = 0
  private pending: StructuredSave<Model> | null = null
  private listeners = new Set<() => void>()
  snapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private publish(patch: Partial<StructuredSnapshot<Model>>) {
    this.state = { ...this.state, ...patch }
    for (const listener of this.listeners) listener()
  }
  open(slug: string) {
    this.pending = null
    const generation = ++this.generation
    this.publish(initialSnapshot<Model>(slug))
    return generation
  }
  close() { this.generation++; this.pending = null }
  receive(generation: number, baseline: StructuredBaseline<Model>) {
    if (generation === this.generation) this.publish({ baseline, model: baseline.model, acknowledged: baseline.loss.lossless, loadError: '' })
  }
  reject(generation: number, error: unknown) {
    if (generation === this.generation) this.publish({ loadError: error instanceof Error ? error.message : String(error) })
  }
  acknowledge() { this.publish({ acknowledged: true }) }
  edit(transform: (model: Model) => Model) {
    if (this.state.acknowledged && this.state.model) this.publish({ model: transform(this.state.model) })
  }
  clearError() { this.publish({ saveError: '' }) }
  beginSave(): StructuredSave<Model> | null {
    const { model, baseline, acknowledged } = this.state
    if (this.pending || !acknowledged || !model || !baseline || model === baseline.model) return null
    const ticket = { generation: this.generation, model, baseline }
    this.pending = ticket
    this.publish({ saving: true, saveError: '' })
    return ticket
  }
  owns(ticket: StructuredSave<Model>) { return this.pending === ticket && ticket.generation === this.generation }
  finish(ticket: StructuredSave<Model>, outcome: { version: number } | { error: string } | null) {
    if (!this.owns(ticket)) return
    this.pending = null
    this.publish({ saving: false, ...(outcome && 'version' in outcome ? { baseline: { ...ticket.baseline, model: ticket.model, version: outcome.version } } : {}), ...(outcome && 'error' in outcome ? { saveError: outcome.error } : {}) })
  }
}
export function structuredSaveError(noun: string, error: unknown): string {
  if (error instanceof ApiError && error.status === 409) return `This ${noun} changed somewhere else (another tab, or the agent) since you opened it. Your edits are still here — reopen it to get the current version, then re-apply them.`
  return error instanceof Error ? error.message : String(error)
}
export interface StructuredTransport<Model> {
  load: (slug: string) => Promise<StructuredBaseline<Model>>
  save: (slug: string, version: number, model: Model) => Promise<{ version: number }>
}
export function useStructuredEditor<Model>(slug: string, noun: string, transport: StructuredTransport<Model>, readOnly?: boolean, onDirty?: (dirty: boolean) => void) {
  const [workspace] = useState(() => new StructuredEditorWorkspace<Model>())
  const state = useSyncExternalStore(workspace.subscribe, workspace.snapshot, workspace.snapshot)
  const dirtyCallback = useRef(onDirty)
  dirtyCallback.current = onDirty
  useEffect(() => {
    const generation = workspace.open(slug)
    transport.load(slug).then(baseline => workspace.receive(generation, baseline)).catch(error => workspace.reject(generation, error))
    return () => workspace.close()
  }, [workspace, slug, transport])
  const ready = state.slug === slug && !!state.model && !!state.baseline
  const dirty = ready && state.model !== state.baseline?.model
  useEffect(() => { dirtyCallback.current?.(dirty) }, [dirty])
  const editable = ready && !readOnly && state.acknowledged
  const reason = readOnly ? 'This version is read-only — open the current version to edit it.' : !state.acknowledged ? 'Read the formatting notice above, then choose “edit anyway”.' : ''
  return {
    ...state, ready, dirty, editable, reason,
    edit: (transform: (model: Model) => Model) => { if (editable) workspace.edit(transform) },
    acknowledge: () => workspace.acknowledge(), clearError: () => workspace.clearError(),
    save: async (confirmLoss: (baseline: StructuredBaseline<Model>) => Promise<boolean>) => {
      if (!editable) return
      const ticket = workspace.beginSave()
      if (!ticket) return
      try {
        if (!ticket.baseline.loss.lossless && !await confirmLoss(ticket.baseline)) { workspace.finish(ticket, null); return }
        if (!workspace.owns(ticket)) return
        const result = await transport.save(slug, ticket.baseline.version, ticket.model)
        workspace.finish(ticket, result)
      } catch (error) { workspace.finish(ticket, { error: structuredSaveError(noun, error) }) }
    },
  }
}
