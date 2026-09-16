import { useEffect, useRef, useSyncExternalStore } from 'react'
import type { EditModeParam } from './editMode'
import type { WidgetAnnotation } from './annotate'
import type { WidgetWireHandlers } from './useWidgetActionBridge'
import { ArtifactEditSession } from './iterationSession'

export interface IterationTarget {
  slug?: string
  persistVersion?: (next: string) => void | Promise<void>
  correction?: (directive: string) => void | Promise<void>
}
export interface ArtifactIteration {
  params: EditModeParam[]; droppedParams: number; values: Record<string, string>
  setValue: (key: string, value: string) => void
  dirty: boolean; save: () => Promise<void>; saving: boolean; savable: boolean
  annotating: boolean; toggleAnnotate: () => void; annotations: WidgetAnnotation[]
  setNote: (index: number, note: string) => void; removeAnnotation: (index: number) => void
  sendCorrection: () => Promise<void>; error: string | null
  wire: Pick<WidgetWireHandlers, 'onEditValues' | 'onEditReady' | 'onAnnotation'>
}

export function useArtifactIteration(frameRef: { current: HTMLIFrameElement | null }, { source, target }: { source: string; target: IterationTarget }): ArtifactIteration {
  const owner = useRef<ArtifactEditSession | null>(null)
  if (!owner.current) owner.current = new ArtifactEditSession(frameRef, source, target)
  const session = owner.current
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot)
  useEffect(() => { session.activate(); return session.dispose }, [session])
  useEffect(() => session.configure(source, target, frameRef), [session, source, target, frameRef])
  return {
    ...state, setValue: session.setValue, save: session.save, toggleAnnotate: session.toggleAnnotate,
    setNote: session.setNote, removeAnnotation: session.removeAnnotation, sendCorrection: session.sendCorrection,
    wire: { onEditValues: session.onEditValues, onEditReady: session.onEditReady, onAnnotation: session.onAnnotation },
  }
}
