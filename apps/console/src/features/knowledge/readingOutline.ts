
export interface OutlineEntry {
  offset: number
  depth: number
  text: string
}

const ATX = /^( {0,3})(#{1,6})(?:[ \t]+(.*?))?[ \t]*$/

const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/

function displayText(raw: string): string {
  return raw
    .replace(/[ \t]+#+[ \t]*$/, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/(\*\*|__)(.+?)\1/g, '$2')
    .replace(/(\*|_)(.+?)\1/g, '$2')
    .replace(/~~(.+?)~~/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

export function parseOutline(markdown: string): OutlineEntry[] {
  const found: Array<{ offset: number; level: number; text: string }> = []
  let fence: { char: string; len: number } | null = null
  let offset = 0

  for (const line of (markdown || '').split('\n')) {
    const f = FENCE.exec(line)
    if (fence) {
      if (f && f[1][0] === fence.char && f[1].length >= fence.len && !f[2].trim()) fence = null
    } else if (f) {
      fence = { char: f[1][0], len: f[1].length }
    } else {
      const m = ATX.exec(line)
      if (m) found.push({ offset: offset + m[1].length, level: m[2].length, text: displayText(m[3] ?? '') })
    }
    offset += line.length + 1
  }

  if (!found.length) return []
  const shallowest = found.reduce((min, h) => (h.level < min ? h.level : min), 6)
  return found.map((h) => ({ offset: h.offset, depth: h.level - shallowest, text: h.text }))
}
