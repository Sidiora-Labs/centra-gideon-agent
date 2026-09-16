
export interface PasteBlock { id: string; seq: number; lines: number; content: string }

export const PASTE_THRESHOLD_LINES = 4
export const PASTE_THRESHOLD_CHARS = 320

export const PASTE_MARKER_RE = /\[Paste #(\d+)\]/g
export const markerFor = (seq: number) => `[Paste #${seq}]`

export function shouldCollapsePaste(text: string): boolean {
  if (!text) return false
  return text.split('\n').length >= PASTE_THRESHOLD_LINES || text.length >= PASTE_THRESHOLD_CHARS
}

export function nextSeq(blocks: PasteBlock[]): number {
  return blocks.reduce((m, b) => Math.max(m, b.seq), 0) + 1
}

export function makePasteId(seq: number): string {
  return `paste-${seq}-${seq * 2654435761 % 100000}`
}

export function expandPasteMarkers(text: string, blocks: PasteBlock[]): string {
  if (!blocks.length) return text
  const bySeq = new Map(blocks.map((b) => [b.seq, b]))
  return text.replace(PASTE_MARKER_RE, (whole, seq) => {
    const b = bySeq.get(Number(seq))
    return b ? b.content : whole
  })
}

export function pruneBlocks(text: string, blocks: PasteBlock[]): PasteBlock[] {
  const present = new Set<number>()
  let m: RegExpExecArray | null
  PASTE_MARKER_RE.lastIndex = 0
  while ((m = PASTE_MARKER_RE.exec(text)) !== null) present.add(Number(m[1]))
  return blocks.filter((b) => present.has(b.seq))
}
