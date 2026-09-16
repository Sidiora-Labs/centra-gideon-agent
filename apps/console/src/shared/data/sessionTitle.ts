import { sessionActivitySeconds } from './epoch'

export interface TitleableSession {
  key: string
  title?: string
  prompt_preview?: string
  last_message?: string
  created?: string
  last_activity_ts?: string
  last_ts?: string
}

const RAW_SESSION_KEY = /^chat-\d+-\d+$/

export function isRawSessionId(title: string | undefined | null, key?: string): boolean {
  const t = (title ?? '').trim()
  if (!t) return true
  if (key && t === key.trim()) return true
  return RAW_SESSION_KEY.test(t)
}

const MAX_SNIPPET = 60

function toSnippet(text: string): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  if (clean.length <= MAX_SNIPPET) return clean
  const cut = clean.slice(0, MAX_SNIPPET)
  const space = cut.lastIndexOf(' ')
  return `${(space >= MAX_SNIPPET - 15 ? cut.slice(0, space) : cut).trimEnd()}…`
}

function relStamp(secs?: number): string {
  if (secs == null) return ''
  const s = Math.max(0, Date.now() / 1000 - secs)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  if (s < 604800) return `${Math.floor(s / 86400)}d`
  return `${Math.floor(s / 604800)}w`
}

export function sessionTitle(s: TitleableSession): string {
  const human = (s.title ?? '').trim()
  if (!isRawSessionId(human, s.key)) return human
  const snippet = (s.prompt_preview ?? '').trim() || (s.last_message ?? '').trim()
  if (snippet) return toSnippet(snippet)
  const when = relStamp(sessionActivitySeconds(s))
  return when ? `Untitled chat · ${when}` : 'Untitled chat'
}
