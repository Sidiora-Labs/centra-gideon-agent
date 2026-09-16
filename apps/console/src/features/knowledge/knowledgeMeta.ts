import { FileText, StickyNote, BookMarked, Bookmark, Code2, Image, Music, Video, FileType2, FileSpreadsheet, Presentation, File, Shapes, Gavel } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { KnowledgeItem, KnowledgeType } from '../../shared/data/api'
import { epochSeconds } from '../../shared/data/epoch'

export interface TypeMeta { key: KnowledgeType; label: string; icon: LucideIcon; tone: string; group: 'text' | 'link' | 'media' | 'document' }
export const TYPES: TypeMeta[] = [
  { key: 'note', label: 'Note', icon: StickyNote, tone: 'var(--color-primary)', group: 'text' },
  { key: 'fleeting', label: 'Fleeting note', icon: FileText, tone: 'var(--color-primary)', group: 'text' },
  { key: 'journal', label: 'Journal', icon: BookMarked, tone: 'var(--color-primary)', group: 'text' },
  { key: 'gist', label: 'Gist', icon: Code2, tone: 'var(--color-info)', group: 'text' },
  { key: 'bookmark', label: 'Bookmark', icon: Bookmark, tone: 'var(--color-info)', group: 'link' },
  { key: 'image', label: 'Image', icon: Image, tone: 'var(--color-ok)', group: 'media' },
  { key: 'audio', label: 'Audio', icon: Music, tone: 'var(--color-ok)', group: 'media' },
  { key: 'video', label: 'Video', icon: Video, tone: 'var(--color-ok)', group: 'media' },
  { key: 'pdf', label: 'PDF', icon: FileType2, tone: 'var(--color-warn)', group: 'document' },
  { key: 'document', label: 'Document', icon: File, tone: 'var(--color-warn)', group: 'document' },
  { key: 'sheet', label: 'Spreadsheet', icon: FileSpreadsheet, tone: 'var(--color-warn)', group: 'document' },
  { key: 'slides', label: 'Slides', icon: Presentation, tone: 'var(--color-warn)', group: 'document' },
]

export const ARTIFACT_TYPE: TypeMeta = {
  key: 'artifact', label: 'Artifact', icon: Shapes, tone: 'var(--color-secondary)', group: 'document',
}

export function isArtifactItem(it: Pick<KnowledgeItem, 'type' | 'item_type'>): boolean {
  return it.type === 'artifact' || (it.item_type || '').toLowerCase() === 'artifact'
}

export const DECISION_TYPE: TypeMeta = {
  key: 'decision', label: 'Decision', icon: Gavel, tone: 'var(--color-secondary)', group: 'text',
}

export function isDecisionItem(it: Pick<KnowledgeItem, 'type' | 'item_type'>): boolean {
  return it.type === 'decision' || (it.item_type || '').toLowerCase() === 'decision'
}

export function resolveType(it: Pick<KnowledgeItem, 'type' | 'item_type' | 'mime_type' | 'url'>): TypeMeta {
  if (isArtifactItem(it)) return ARTIFACT_TYPE
  if (isDecisionItem(it)) return DECISION_TYPE
  const explicit = it.type && TYPES.find((t) => t.key === it.type)
  if (explicit) return explicit
  const raw = (it.item_type || '').toLowerCase()
  const byRaw = TYPES.find((t) => t.key === raw)
  if (byRaw) return byRaw
  const mime = (it.mime_type || '').toLowerCase()
  if (mime.startsWith('image/')) return typeMeta('image')
  if (mime.startsWith('audio/')) return typeMeta('audio')
  if (mime.startsWith('video/')) return typeMeta('video')
  if (mime.includes('pdf')) return typeMeta('pdf')
  if (mime.includes('spreadsheet') || mime.includes('excel') || mime.includes('csv')) return typeMeta('sheet')
  if (mime.includes('presentation') || mime.includes('powerpoint')) return typeMeta('slides')
  if (mime.includes('word') || mime.includes('document')) return typeMeta('document')
  if (it.url) return typeMeta('bookmark')
  return typeMeta('note')
}
export function typeMeta(k: KnowledgeType): TypeMeta { return TYPES.find((t) => t.key === k) ?? TYPES[0] }

const _LANG_DISPLAY: Record<string, string> = {
  typescript: 'TypeScript', javascript: 'JavaScript', python: 'Python', go: 'Go',
  rust: 'Rust', java: 'Java', c: 'C', cpp: 'C++', html: 'HTML', css: 'CSS',
  sql: 'SQL', bash: 'Bash', json: 'JSON', yaml: 'YAML', markdown: 'Markdown',
}
export function languageLabel(lang: string): string {
  const k = lang.trim().toLowerCase()
  return _LANG_DISPLAY[k] || (lang.charAt(0).toUpperCase() + lang.slice(1))
}

export function typeLabel(it: Pick<KnowledgeItem, 'type' | 'item_type' | 'mime_type' | 'url' | 'gist_language'>): string {
  const tm = resolveType(it)
  if (tm.key === 'gist' && (it.gist_language || '').trim()) {
    return `${tm.label} · ${languageLabel(it.gist_language!)}`
  }
  return tm.label
}

const _titleCase = (k: string) => k.replace(/[_-]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

export function insightRows(insights?: Record<string, unknown> | null): Array<{ label: string; value: string }> {
  if (!insights || typeof insights !== 'object') return []
  const out: Array<{ label: string; value: string }> = []
  for (const [k, v] of Object.entries(insights)) {
    if (v == null || v === '') continue
    const label = _titleCase(k)
    if (Array.isArray(v)) {
      if (v.length) out.push({ label, value: v.map((x) => (x && typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ') })
    } else if (typeof v === 'object') {
      const parts = Object.entries(v as Record<string, unknown>)
        .filter(([, vv]) => vv != null && vv !== '')
        .map(([kk, vv]) => `${_titleCase(kk)}: ${Array.isArray(vv) ? vv.join(', ') : String(vv)}`)
      if (parts.length) out.push({ label, value: parts.join(' · ') })
    } else {
      const value = String(v)
      if (value.trim()) out.push({ label, value })
    }
  }
  return out
}

export type CreateKind = 'text' | 'gist' | 'bookmark' | 'file'
export function createKind(t: KnowledgeType): CreateKind {
  if (t === 'gist') return 'gist'
  if (t === 'bookmark') return 'bookmark'
  if (['image', 'audio', 'video', 'pdf', 'document', 'sheet', 'slides'].includes(t)) return 'file'
  return 'text'
}

export const ACCEPTED_MIMES: Record<string, string> = {
  image: 'image/png,image/jpeg,image/gif,image/webp,image/bmp,image/svg+xml',
  audio: 'audio/mpeg,audio/wav,audio/ogg,audio/flac,audio/mp4,audio/x-m4a,audio/webm',
  video: 'video/mp4,video/quicktime,video/x-msvideo,video/x-matroska,video/webm,video/x-m4v,.m4v',
  pdf: 'application/pdf',
  document: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/msword,text/plain,text/markdown,.markdown,.text',
  sheet: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,text/csv,text/tab-separated-values,.tsv',
  slides: 'application/vnd.openxmlformats-officedocument.presentationml.presentation,application/vnd.ms-powerpoint',
}

export const GIST_LANGUAGES = ['typescript', 'javascript', 'python', 'go', 'rust', 'java', 'c', 'cpp', 'html', 'css', 'sql', 'bash', 'json', 'yaml', 'markdown']

export function fmtBytes(n?: number): string {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function relTime(iso?: string): string {
  const secs = epochSeconds(iso)
  if (secs === undefined) return ''
  const t = secs * 1000
  const s = Date.now() / 1000 - secs
  if (s < 0) return new Date(t).toLocaleDateString()
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`
  return new Date(t).toLocaleDateString()
}
