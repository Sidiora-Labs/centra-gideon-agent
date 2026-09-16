import type { ComponentType, LazyExoticComponent } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { IterationTarget } from '../widget/useArtifactIteration'

export interface PreviewCapability {
  render: LazyExoticComponent<ComponentType<PreviewProps>> | ComponentType<PreviewProps>
  streaming?: boolean
  sandboxed?: boolean
}

export interface EditCapability {
  language: string
  split?: boolean
  render?: ComponentType<DocumentEditorProps>
}

export interface DocumentEditorProps {
  slug: string
  title: string
  mode: 'dark' | 'light'
  readOnly?: boolean
  onDirty?: (dirty: boolean) => void
}

export interface GenerateCapability {
  tool?: string
  skill?: string
}

export interface SecurityCapability {
  sandbox?: boolean
  sanitize?: boolean
}

export interface ExportTarget {
  id: string
  label: string
  run: (content: string, title: string) => void | Promise<void>
}

export interface PreviewProps {
  content: string
  mode: 'dark' | 'light'
  title: string
  path?: string
  streaming?: boolean
  iterate?: IterationTarget
}

export interface EmbedProps {
  content: string
  title: string
  slug?: string
  messageTs?: string
  widgetIndex?: number
  streaming?: boolean
}

export interface EmbedCapability {
  render: LazyExoticComponent<ComponentType<EmbedProps>> | ComponentType<EmbedProps>
  streaming?: boolean
}

export interface ContentType {
  id: string
  label: string
  icon: LucideIcon
  tone: string
  kinds?: string[]
  exts?: string[]
  mimes?: string[]
  match?: (probe: ContentProbe) => boolean
  preview?: PreviewCapability
  embed?: EmbedCapability
  edit?: EditCapability
  generate?: GenerateCapability
  security?: SecurityCapability
  exports?: ExportTarget[]
  commentable?: boolean
  binary?: boolean
}

export interface ContentProbe {
  kind?: string
  name?: string
  mime?: string
}

const entries = new Map<string, ContentType>()
export function registerContentType(type: ContentType): void {
  entries.delete(type.id)
  entries.set(type.id, type)
}
export function getContentType(id: string): ContentType | undefined { return entries.get(id) }
export function allContentTypes(): readonly ContentType[] { return Array.from(entries.values()) }

export function resolveContentType(probe: ContentProbe, fallback = 'text'): ContentType {
  const candidates = allContentTypes()
  const extension = probe.name ? extOf(probe.name) : ''
  const tiers = [
    (type: ContentType) => !!type.match?.(probe),
    (type: ContentType) => !!probe.kind && !!type.kinds?.includes(probe.kind),
    (type: ContentType) => !!extension && !!type.exts?.includes(extension),
    (type: ContentType) => !!probe.mime && !!type.mimes?.some(prefix => probe.mime!.startsWith(prefix)),
  ]
  for (const accepts of tiers) {
    const match = candidates.find(accepts)
    if (match) return match
  }
  return entries.get(fallback) ?? candidates[0]
}
export function embedFor(kind: string | undefined): EmbedCapability | undefined {
  return entries.get(kind === 'react' ? kind : kind && entries.has(kind) ? kind : 'widget')?.embed
}
export function isEditable(type: ContentType): boolean { return Boolean(type.edit) }
export function isSandboxed(type: ContentType): boolean { return Boolean(type.preview?.sandboxed) }
export function isCommentable(type: ContentType): boolean { return type.commentable ?? (!!type.preview && !type.preview.sandboxed) }
export function extOf(name: string): string {
  const filename = name.slice(name.lastIndexOf('/') + 1) || name
  const extension = filename.lastIndexOf('.')
  return extension > 0 ? filename.substring(extension + 1).toLowerCase() : ''
}
