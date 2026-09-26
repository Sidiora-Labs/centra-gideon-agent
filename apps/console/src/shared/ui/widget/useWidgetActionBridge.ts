import { useEffect, useRef, type RefObject } from 'react'
import { launchChat } from '../../../app/shell/appSdk'
import { WIDGET_ACTION_EVENT, composeWidgetActionText, publishWidgetAction, type WidgetActionMeta } from './actionTurn'
import { EDIT_KEY_RE } from './editMode'
import { readAnnotation, type WidgetAnnotation } from './annotate'

export { MAX_ACTION_TEXT_BYTES, WIDGET_ACTION_EVENT, composeWidgetActionText, finishActionText, publishWidgetAction } from './actionTurn'
export type { WidgetActionMeta } from './actionTurn'

export type WidgetWireMessage =
  | { type: 'widget-height'; height: number; width?: number }
  | { type: 'widget-action'; action: string; payload: unknown }
  | { type: 'widget-error'; message: string }
  | { type: 'widget-ready' }
  | { type: 'widget-edit-values'; values: Record<string, string> }
  | { type: 'widget-edit-ready' }
  | { type: 'widget-annotation'; annotation: WidgetAnnotation }

export function readWidgetMessage(event: MessageEvent, frame: HTMLIFrameElement | null): WidgetWireMessage | null {
  if (!frame?.contentWindow || event.source !== frame.contentWindow) return null
  const data: unknown = event.data
  if (!data || typeof data !== 'object' || Array.isArray(data)) return null
  const value = data as Record<string, unknown>
  switch (value.type) {
    case 'widget-height': {
      const { height, width } = value
      if (typeof height !== 'number' || !Number.isFinite(height)) return null
      return { type: value.type, height, width: typeof width === 'number' && Number.isFinite(width) && width > 0 ? width : undefined }
    }
    case 'widget-action':
      return typeof value.action === 'string' && value.action ? { type: value.type, action: value.action, payload: value.payload } : null
    case 'widget-ready': return { type: value.type }
    case 'widget-error':
      return { type: value.type, message: String(value.message || 'Render error') }
    case 'widget-edit-ready':
      return { type: value.type }
    case 'widget-edit-values': {
      if (!value.values || typeof value.values !== 'object' || Array.isArray(value.values)) return null
      const accepted = Object.entries(value.values).filter(([key, entry]) => EDIT_KEY_RE.test(key) && typeof entry === 'string').slice(0, 32)
      return { type: value.type, values: Object.fromEntries(accepted.map(([key, entry]) => [key, (entry as string).slice(0, 200)])) }
    }
    case 'widget-annotation': {
      const annotation = readAnnotation(value)
      return annotation ? { type: value.type, annotation } : null
    }
    default: return null
  }
}

export interface WidgetWireHandlers {
  forwardActions?: boolean
  onHeight?: (height: number, width?: number) => void
  onError?: (message: string) => void
  onReady?: () => void
  liveArtifact?: () => { saved: boolean; slug: string }
  onEditValues?: (values: Record<string, string>) => void
  onEditReady?: () => void
  onAnnotation?: (annotation: WidgetAnnotation) => void
}

function deliver(message: WidgetWireMessage, handlers: WidgetWireHandlers) {
  switch (message.type) {
    case 'widget-height': return handlers.onHeight?.(message.height, message.width)
    case 'widget-error': return handlers.onError?.(message.message)
    case 'widget-ready': return handlers.onReady?.()
    case 'widget-edit-ready': return handlers.onEditReady?.()
    case 'widget-edit-values': return handlers.onEditValues?.(message.values)
    case 'widget-annotation': return handlers.onAnnotation?.(message.annotation)
    case 'widget-action': {
      if (!handlers.forwardActions) return
      const artifact = handlers.liveArtifact?.()
      const text = composeWidgetActionText(message.action, message.payload, artifact)
      if (text !== null) publishWidgetAction(text, artifact?.saved ? { slug: artifact.slug } : {})
    }
  }
}

export function useWidgetWire(frameRef: RefObject<HTMLIFrameElement | null>, handlers: WidgetWireHandlers): void {
  const current = useRef(handlers)
  current.current = handlers
  useEffect(() => {
    const receive = (event: MessageEvent) => {
      const message = readWidgetMessage(event, frameRef.current)
      if (message) deliver(message, current.current)
    }
    window.addEventListener('message', receive)
    return () => window.removeEventListener('message', receive)
  }, [frameRef])
}

type Consumer = (text: string, meta: WidgetActionMeta) => void
type Subscription = { priority: number; consume: Consumer }
const consumers = new Set<Subscription>()

function receiveAction(event: Event) {
  const detail = (event as CustomEvent).detail
  if (typeof detail?.text !== 'string' || !detail.text.trim()) return
  let owner: Subscription | undefined
  for (const candidate of consumers) {
    if (!owner || candidate.priority >= owner.priority) owner = candidate
  }
  owner?.consume(detail.text, {
    slug: typeof detail.slug === 'string' ? detail.slug : undefined,
    label: typeof detail.label === 'string' && detail.label.trim() ? detail.label : undefined,
  })
}

function useActionConsumer(priority: number, consume: Consumer) {
  const latest = useRef(consume)
  latest.current = consume
  useEffect(() => {
    const subscription = { priority, consume: (text: string, meta: WidgetActionMeta) => latest.current(text, meta) }
    if (!consumers.size) window.addEventListener(WIDGET_ACTION_EVENT, receiveAction)
    consumers.add(subscription)
    return () => {
      consumers.delete(subscription)
      if (!consumers.size) window.removeEventListener(WIDGET_ACTION_EVENT, receiveAction)
    }
  }, [priority])
}

export interface PendingWidgetAction { text: string; label?: string }
let pending: { action: PendingWidgetAction; expires: number } | undefined

export function useWidgetActionBridge(onAction: Consumer): void {
  useActionConsumer(1, onAction)
}

export function useWidgetActionLauncher(): void {
  useActionConsumer(0, (text, meta) => {
    pending = { action: { text, label: meta.label }, expires: Date.now() + 20_000 }
    launchChat()
  })
}

export function takePendingWidgetAction(): PendingWidgetAction | null {
  const transfer = pending
  pending = undefined
  return transfer && Date.now() <= transfer.expires ? transfer.action : null
}
