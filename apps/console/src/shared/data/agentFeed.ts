
export function toolDetail(inputPreview: string, purpose: string): string {
  const raw = inputPreview.trim()
  if (raw.startsWith('{')) {
    try {
      const j = JSON.parse(raw) as Record<string, unknown>
      const key = ['command', 'cmd', 'query', 'pattern', 'url', 'path', 'file_path', 'file', 'name']
        .find((k) => typeof j[k] === 'string' && (j[k] as string).trim())
      if (key) return String(j[key]).replace(/\s+/g, ' ').trim().slice(0, 140)
      if (purpose.trim()) return purpose.trim().slice(0, 140)
      return ''
    } catch {
      const m = raw.match(/"(?:command|cmd|query|pattern|url|path|file_path|file|name)"\s*:\s*"([^"]+)"/)
      if (m) return m[1].replace(/\s+/g, ' ').trim().slice(0, 140)
      if (purpose.trim()) return purpose.trim().slice(0, 140)
      return ''
    }
  }
  const src = raw || purpose.trim()
  return src.split('\n').map((s) => s.trim()).filter(Boolean)[0]?.slice(0, 140) ?? ''
}

export function cleanSay(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/```[\s\S]*$/, ' ')

    .replace(/<\/?[a-zA-Z][^>]*>/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
    .slice(-1200)
}
