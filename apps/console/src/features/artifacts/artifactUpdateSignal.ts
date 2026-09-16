import type { WsMessage } from '../../shared/data/useChatSocket'

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function slugFrom(v: unknown): string | null {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return null
  const s = (v as Record<string, unknown>).slug
  return typeof s === 'string' && s ? s : null
}

export function isArtifactUpdateFor(m: WsMessage, slug: string): boolean {
  if (!slug) return false
  if (m.type !== 'tool_call') return false
  const data = (m.data ?? {}) as Record<string, unknown>
  if (String(data.tool ?? '') !== 'artifact_update') return false
  const named = slugFrom(data.input) ?? slugFrom(data.input_preview)
  if (named) return named === slug
  const preview = typeof data.input_preview === 'string' ? data.input_preview : ''
  if (preview) {
    return new RegExp(`(^|[^A-Za-z0-9_-])${escapeRe(slug)}([^A-Za-z0-9_-]|$)`).test(preview)
  }
  return true
}
