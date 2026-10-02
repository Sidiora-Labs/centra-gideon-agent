import { createContext, useCallback, useContext, useEffect, useMemo, useState, useSyncExternalStore, type ReactNode } from 'react'
import type { GenUiHost, GenUiProducer } from './actions'
import type { GenUiElementState, GenUiV2Element, GenUiV2Envelope } from './envelope'

export type GenUiElementStateUpdate = Partial<GenUiElementState> | ((state: GenUiElementState) => Partial<GenUiElementState>)
export const GenUiElementCtx = createContext<string | null>(null)

type GenUiStateHost = Pick<GenUiHost, 'producer'> & { scopeId?: string; conversationId?: string }
type StateMap = Record<string, GenUiElementState>
type StoredState = { revision: number; types: Record<string, string>; state: StateMap }
type Session = { envelope: GenUiV2Envelope | null; state: StateMap; error: string }
type WidgetStateContextValue = Session & {
  elements: Record<string, GenUiV2Element>
  setElementState: (id: string, update: GenUiElementStateUpdate) => void
}

const EMPTY_STATE: StateMap = Object.freeze({})
const EMPTY_ELEMENTS: Record<string, GenUiV2Element> = Object.freeze({})
const WidgetStateCtx = createContext<WidgetStateContextValue | null>(null)
const IDENTIFIER = /^[A-Za-z][A-Za-z0-9_.-]{0,127}$/
const MAX_TEXT_LENGTH = 4096
const MAX_FIELDS = 128
const PROHIBITED_KEYS = new Set(['__proto__', 'constructor', 'prototype'])
type RevisionAuthority = { revision: number; listeners: Set<() => void> }
const revisionAuthorities = new Map<string, RevisionAuthority>()

function revisionAuthority(key: string): RevisionAuthority {
  let authority = revisionAuthorities.get(key)
  if (!authority) {
    authority = { revision: readStored(key)?.revision ?? 0, listeners: new Set() }
    revisionAuthorities.set(key, authority)
  }
  return authority
}

function publishRevision(key: string, revision: number): number {
  const authority = revisionAuthority(key)
  if (revision <= authority.revision) return authority.revision
  authority.revision = revision
  for (const listener of authority.listeners) listener()
  return revision
}

function subscribeRevision(key: string, listener: () => void): () => void {
  const authority = revisionAuthority(key)
  authority.listeners.add(listener)
  return () => {
    authority.listeners.delete(listener)
    if (!authority.listeners.size) revisionAuthorities.delete(key)
  }
}

function producerIdentity(producer: GenUiProducer): string {
  if (producer.kind === 'workflow-gate') return `workflow-gate:${producer.runId}`
  if (producer.kind === 'tile') return `tile:${producer.viewId}:${producer.ref}`
  return 'chat'
}

export function getGenUiPersistenceKey(host: GenUiStateHost, presentationId: string): string | null {
  const scope = host.scopeId?.trim()
  const conversation = host.conversationId?.trim()
  if (!scope || !conversation) return null
  const identity = JSON.stringify([producerIdentity(host.producer), scope, conversation, presentationId])
  return `gideon.genui.v2:${encodeURIComponent(identity)}`
}

function declaredFields(element?: GenUiV2Element): Set<string> | null {
  if (!element || (element.type !== 'Form' && element.type !== 'ActionPreview')) return element ? new Set() : null
  return new Set(Array.isArray(element.props.fields) ? element.props.fields.filter((field): field is string => typeof field === 'string') : [])
}

function cleanElementState(value: unknown, element?: GenUiV2Element): GenUiElementState {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  const source = value as Record<string, unknown>
  const result: GenUiElementState = {}
  if (typeof source.selected === 'string' && IDENTIFIER.test(source.selected)) result.selected = source.selected
  if (typeof source.filter === 'string') result.filter = source.filter.slice(0, MAX_TEXT_LENGTH)
  if (source.fields && typeof source.fields === 'object' && !Array.isArray(source.fields)) {
    const allowed = declaredFields(element)
    const fields = Object.fromEntries(Object.entries(source.fields as Record<string, unknown>)
      .filter(([key, field]) => typeof field === 'string'
        && key.length > 0 && key.length <= MAX_TEXT_LENGTH && !PROHIBITED_KEYS.has(key)
        && (!allowed || allowed.has(key)))
      .slice(0, MAX_FIELDS)
      .map(([key, field]) => [key, (field as string).slice(0, MAX_TEXT_LENGTH)])) as Record<string, string>
    result.fields = fields
  }
  return result
}

function readStored(key: string | null): StoredState | null {
  if (!key) return null
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? 'null') as Partial<StoredState> | null
    if (!value || !Number.isInteger(value.revision) || (value.revision ?? 0) < 1 || !value.types || !value.state) return null
    return {
      revision: value.revision!,
      types: Object.fromEntries(Object.entries(value.types).filter((entry): entry is [string, string] => typeof entry[1] === 'string')),
      state: Object.fromEntries(Object.entries(value.state).map(([id, state]) => [id, cleanElementState(state)])),
    }
  } catch { return null }
}

function elementTypes(envelope: GenUiV2Envelope): Record<string, string> {
  return Object.fromEntries(Object.entries(envelope.elements).map(([id, element]) => [id, element.type]))
}

function stateForEnvelope(envelope: GenUiV2Envelope, state: StateMap): StateMap {
  const bounded: StateMap = {}
  for (const [id, element] of Object.entries(envelope.elements)) {
    if (!state[id]) continue
    const cleaned = cleanElementState(state[id], element)
    if (Object.keys(cleaned).length) bounded[id] = cleaned
  }
  return bounded
}

function reconcile(envelope: GenUiV2Envelope, previous: StateMap, previousTypes: Record<string, string>): StateMap {
  const next: StateMap = {}
  for (const [id, element] of Object.entries(envelope.elements)) {
    const declared = cleanElementState(envelope.state[id], element)
    if (previousTypes[id] !== element.type || !previous[id]) {
      if (Object.keys(declared).length) next[id] = declared
      continue
    }
    const retained = cleanElementState(previous[id], element)
    const fields = declared.fields || retained.fields ? { ...declared.fields, ...retained.fields } : undefined
    next[id] = { ...declared, ...retained, ...(fields ? { fields } : {}) }
  }
  return next
}

function initialSession(envelope: GenUiV2Envelope, stored: StoredState | null): Session {
  if (stored && stored.revision > envelope.revision) {
    return { envelope: null, state: {}, error: `Rejected stale GenUI revision ${envelope.revision}; revision ${stored.revision} is already stored.` }
  }
  return {
    envelope,
    state: reconcile(envelope, stored?.state ?? {}, stored?.types ?? {}),
    error: '',
  }
}

export function GenUiWidgetStateProvider({ envelope, host, children }: {
  envelope: GenUiV2Envelope
  host: GenUiStateHost
  children: ReactNode
}) {
  const storageKey = getGenUiPersistenceKey(host, envelope.id)
  const stored = useMemo(() => readStored(storageKey), [storageKey])
  const [session, setSession] = useState<Session>(() => initialSession(envelope, stored))
  const subscribe = useCallback((listener: () => void) => storageKey ? subscribeRevision(storageKey, listener) : () => {}, [storageKey])
  const snapshot = useCallback(() => storageKey ? revisionAuthority(storageKey).revision : envelope.revision, [envelope.revision, storageKey])
  const authorityRevision = useSyncExternalStore(subscribe, snapshot, snapshot)

  useEffect(() => {
    setSession(current => {
      const acceptedRevision = Math.max(current.envelope?.revision ?? 0, stored?.revision ?? 0, authorityRevision)
      if (envelope.revision < acceptedRevision) {
        const error = `Rejected stale GenUI revision ${envelope.revision}; revision ${acceptedRevision} is already active.`
        return current.envelope?.revision === acceptedRevision ? { ...current, error } : { envelope: null, state: {}, error }
      }
      const priorEnvelope = current.envelope
      const priorTypes = priorEnvelope ? elementTypes(priorEnvelope) : stored?.types ?? {}
      const priorState = priorEnvelope ? current.state : stored?.state ?? {}
      return { envelope, state: reconcile(envelope, priorState, priorTypes), error: '' }
    })
  }, [authorityRevision, envelope, stored])

  useEffect(() => {
    if (!storageKey || !session.envelope) return
    if (session.envelope.revision < publishRevision(storageKey, session.envelope.revision)) return
    const currentStored = readStored(storageKey)
    if (currentStored && currentStored.revision > session.envelope.revision) {
      publishRevision(storageKey, currentStored.revision)
      return
    }
    try {
      localStorage.setItem(storageKey, JSON.stringify({
        revision: session.envelope.revision,
        types: elementTypes(session.envelope),
        state: stateForEnvelope(session.envelope, session.state),
      } satisfies StoredState))
    } catch { /* local persistence is best effort */ }
  }, [session, storageKey])

  const setElementState = useCallback((id: string, update: GenUiElementStateUpdate) => {
    setSession(current => {
      if (!current.envelope?.elements[id]) return current
      const before = current.state[id] ?? {}
      const patch = typeof update === 'function' ? update(before) : update
      return { ...current, state: { ...current.state, [id]: cleanElementState({ ...before, ...patch }, current.envelope.elements[id]) } }
    })
  }, [])

  const value = useMemo<WidgetStateContextValue>(() => ({
    ...(storageKey && session.envelope && session.envelope.revision < authorityRevision
      ? { envelope: null, state: {}, error: `Rejected stale GenUI revision ${session.envelope.revision}; revision ${authorityRevision} is already active.` }
      : session),
    elements: storageKey && session.envelope && session.envelope.revision < authorityRevision
      ? EMPTY_ELEMENTS : session.envelope?.elements ?? EMPTY_ELEMENTS,
    setElementState,
  }), [authorityRevision, session, setElementState, storageKey])
  return <WidgetStateCtx.Provider value={value}>{children}</WidgetStateCtx.Provider>
}

export function useGenUiElementState(): { state: GenUiElementState; setState: (update: GenUiElementStateUpdate) => void } {
  const id = useContext(GenUiElementCtx)
  const widget = useContext(WidgetStateCtx)
  const [fallback, setFallback] = useState<GenUiElementState>({})
  const setState = useCallback((update: GenUiElementStateUpdate) => {
    if (id && widget) widget.setElementState(id, update)
    else setFallback(current => cleanElementState({ ...current, ...(typeof update === 'function' ? update(current) : update) }))
  }, [id, widget])
  return { state: id && widget ? widget.state[id] ?? EMPTY_STATE : fallback, setState }
}

export function useGenUiWidgetState(): { state: StateMap; elements: Record<string, GenUiV2Element> } {
  const widget = useContext(WidgetStateCtx)
  return { state: widget?.state ?? EMPTY_STATE, elements: widget?.elements ?? EMPTY_ELEMENTS }
}

export function useGenUiWidgetEnvelope(): Pick<WidgetStateContextValue, 'envelope' | 'error'> {
  const widget = useContext(WidgetStateCtx)
  return { envelope: widget?.envelope ?? null, error: widget?.error ?? '' }
}
