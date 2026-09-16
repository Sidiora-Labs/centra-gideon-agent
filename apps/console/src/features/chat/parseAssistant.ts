
export function parseOptions(text: string): { body: string; options: string[] } {
  const re = /\[\s*OPTIONS?\s*:\s*([^\]]+)\]\s*$/i
  const m = text.match(re)
  if (!m) return { body: text, options: [] }
  const options = m[1].split('|').map((s) => s.trim()).filter(Boolean)
  return { body: text.slice(0, m.index).trimEnd(), options }
}

export function parseSwitchToAgent(text: string): { body: string; switchTo: string | null } {
  const re = /\[\s*SWITCH_TO_AGENT\s*:?\s*([^\]]*)\]\s*$/i
  const m = text.match(re)
  if (!m) return { body: text, switchTo: null }
  return { body: text.slice(0, m.index).trimEnd(), switchTo: m[1].trim() }
}

const FILE_RE = /(?:^|[\s(`'"])((?:~|\/)[\w./\-]+\.\w{1,8}|[\w./\-]+\/[\w./\-]+\.\w{1,8})/g

export interface TextPart { kind: 'text' | 'file'; value: string }

export function splitFileRefs(text: string): TextPart[] {
  const parts: TextPart[] = []
  let last = 0
  for (const m of text.matchAll(FILE_RE)) {
    const path = m[1]
    const start = m.index! + m[0].indexOf(path)
    if (start > last) parts.push({ kind: 'text', value: text.slice(last, start) })
    parts.push({ kind: 'file', value: path })
    last = start + path.length
  }
  if (last < text.length) parts.push({ kind: 'text', value: text.slice(last) })
  return parts.length ? parts : [{ kind: 'text', value: text }]
}
