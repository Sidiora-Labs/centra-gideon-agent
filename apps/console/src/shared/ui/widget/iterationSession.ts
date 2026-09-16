import { composeCorrectionDirective, MAX_ANNOTATIONS, type WidgetAnnotation } from './annotate'
import { finishActionText, publishWidgetAction } from './actionTurn'
import { sanitizeCssValue } from './cssSanitize'
import { EDIT_MODE_ANNOTATE, EDIT_MODE_READ_KEYS, EDIT_MODE_SET_KEYS, parseEditModeBlock, rewriteEditModeBlock, type EditModeParam } from './editMode'
import type { IterationTarget } from './useArtifactIteration'

interface SessionState {
  params: EditModeParam[]; droppedParams: number; values: Record<string, string>
  dirty: boolean; saving: boolean; savable: boolean; annotating: boolean
  annotations: WidgetAnnotation[]; error: string | null
}
type FrameRef = { current: HTMLIFrameElement | null }
const errorText = (error: unknown) => String(error && typeof error === 'object' && 'message' in error ? error.message : error)

export class ArtifactEditSession {
  private state: SessionState = { params: [], droppedParams: 0, values: {}, dirty: false, saving: false, savable: false, annotating: false, annotations: [], error: null }
  private listeners = new Set<() => void>()
  private updates = new Map<string, string>()
  private scheduled: number | null = null
  private reader: { resolve: (values: Record<string, string>) => void; reject: (reason: Error) => void; timer: number } | null = null
  private source = ''
  private signature = ''
  private generation = 0
  private active = true
  private correcting = false
  private saveLocked = false
  private editRevision = 0

  constructor(private frame: FrameRef, source: string, private target: IterationTarget) { this.configure(source, target, frame) }
  getSnapshot = () => this.state
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  activate = () => { this.active = true }
  private update(patch: Partial<SessionState>) {
    if (!this.active) return
    this.state = { ...this.state, ...patch }
    for (const notify of this.listeners) notify()
  }
  private post(data: Record<string, unknown>) { this.frame.current?.contentWindow?.postMessage(data, '*') }
  configure(source: string, target: IterationTarget, frame: FrameRef) {
    this.target = target
    this.frame = frame
    if (source === this.source && this.state.savable === (!!target.persistVersion && this.state.params.length > 0)) return
    const changed = source !== this.source
    this.source = source
    const block = parseEditModeBlock(source)
    const params = block?.params ?? []
    const signature = JSON.stringify(params.map(({ key, value }) => [key, value]))
    const patch: Partial<SessionState> = { params, droppedParams: block?.dropped ?? 0, savable: !!target.persistVersion && params.length > 0 }
    if (signature !== this.signature) {
      this.signature = signature
      patch.values = Object.fromEntries(params.map(({ key, value }) => [key, value]))
      patch.dirty = false
    }
    if (changed) {
      this.generation++
      this.cancelRead('the preview changed before its values were saved')
      this.cancelScheduled()
      this.updates.clear()
    }
    this.update(patch)
  }
  private cancelScheduled() {
    if (this.scheduled !== null) cancelAnimationFrame(this.scheduled)
    this.scheduled = null
  }
  flush = () => {
    this.cancelScheduled()
    if (!this.updates.size) return
    const edits = Array.from(this.updates, ([key, value]) => ({ key, value }))
    this.updates.clear()
    this.post({ type: EDIT_MODE_SET_KEYS, edits })
  }
  setValue = (key: string, input: string) => {
    const value = sanitizeCssValue(input)
    if (!value || !this.state.params.some(param => param.key === key)) return
    this.editRevision++
    this.update({ values: { ...this.state.values, [key]: value }, dirty: true })
    this.updates.set(key, value)
    if (this.scheduled === null) this.scheduled = requestAnimationFrame(this.flush)
  }
  onEditReady = () => {
    const edits = this.state.params.map(({ key, value }) => ({ key, value }))
    if (edits.length) this.post({ type: EDIT_MODE_SET_KEYS, edits })
  }
  onEditValues = (values: Record<string, string>) => {
    const pending = this.reader
    this.reader = null
    if (!pending) return
    window.clearTimeout(pending.timer)
    pending.resolve(values)
  }
  private cancelRead(reason: string) {
    const pending = this.reader
    this.reader = null
    if (pending) { window.clearTimeout(pending.timer); pending.reject(new Error(reason)) }
  }
  private readValues(keys: string[]) {
    return new Promise<Record<string, string>>((resolve, reject) => {
      const timer = window.setTimeout(() => this.cancelRead('the preview did not report its live values'), 2000)
      this.reader = { resolve, reject, timer }
      this.post({ type: EDIT_MODE_READ_KEYS, keys })
    })
  }
  save = async (): Promise<void> => {
    const persist = this.target.persistVersion
    if (!persist || this.saveLocked || !this.state.params.length) return
    this.saveLocked = true
    const generation = this.generation
    const source = this.source
    const editRevision = this.editRevision
    const keys = this.state.params.map(param => param.key)
    this.update({ saving: true, error: null })
    try {
      this.flush()
      const observed = await this.readValues(keys)
      if (generation !== this.generation || !this.active) return
      const accepted = Object.fromEntries(keys.flatMap(key => typeof observed[key] === 'string' && observed[key].trim() ? [[key, observed[key].trim()]] : []))
      await persist(rewriteEditModeBlock(source, accepted))
      if (generation === this.generation && editRevision === this.editRevision) this.update({ dirty: false })
    } catch (error) {
      if (generation === this.generation) this.update({ error: `Couldn't save the tweaks: ${errorText(error)}` })
    } finally {
      this.saveLocked = false
      this.update({ saving: false })
    }
  }
  toggleAnnotate = () => {
    const annotating = !this.state.annotating
    this.update({ annotating })
    this.post({ type: EDIT_MODE_ANNOTATE, on: annotating })
  }
  onAnnotation = (annotation: WidgetAnnotation) => {
    if (this.state.annotations.length < MAX_ANNOTATIONS) this.update({ annotations: [...this.state.annotations, annotation] })
  }
  setNote = (index: number, note: string) => this.update({ annotations: this.state.annotations.map((item, at) => at === index ? { ...item, note } : item) })
  removeAnnotation = (index: number) => this.update({ annotations: this.state.annotations.filter((_, at) => at !== index) })
  sendCorrection = async (): Promise<void> => {
    if (this.correcting || !this.state.annotations.length) return
    this.correcting = true
    const annotations = this.state.annotations
    const directive = composeCorrectionDirective(annotations)
    const { correction, slug } = this.target
    this.update({ error: null })
    try {
      if (correction) await correction(directive)
      else publishWidgetAction(finishActionText(directive, slug ? { saved: true, slug } : undefined), slug ? { slug } : {})
      this.update({ annotations: this.state.annotations.filter(annotation => !annotations.includes(annotation)) })
      if (this.state.annotating) this.toggleAnnotate()
    } catch (error) {
      this.update({ error: `Couldn't send the correction: ${errorText(error)}` })
    } finally { this.correcting = false }
  }
  dispose = () => {
    this.active = false
    this.generation++
    this.cancelScheduled()
    this.updates.clear()
    this.cancelRead('the preview closed before its values were saved')
  }
}
