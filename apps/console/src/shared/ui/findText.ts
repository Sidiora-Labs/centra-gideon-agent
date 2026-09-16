
export interface TextMatch {
  start: number
  end: number
}

function fold(text: string): string {
  let out = ''
  for (const ch of text) out += ch.toLowerCase()
  return out
}

function foldWithMap(text: string): { folded: string; srcStart: number[]; srcEnd: number[] } {
  let folded = ''
  const srcStart: number[] = []
  const srcEnd: number[] = []
  let at = 0
  for (const ch of text) {
    const lower = ch.toLowerCase()
    folded += lower
    for (let i = 0; i < lower.length; i++) { srcStart.push(at); srcEnd.push(at + ch.length) }
    at += ch.length
  }
  return { folded, srcStart, srcEnd }
}

export function findInText(text: string, query: string): TextMatch[] {
  if (!query.trim()) return []
  const needle = fold(query)
  const { folded, srcStart, srcEnd } = foldWithMap(text)
  const out: TextMatch[] = []
  let from = 0
  for (;;) {
    const at = folded.indexOf(needle, from)
    if (at < 0) break
    out.push({ start: srcStart[at], end: srcEnd[at + needle.length - 1] })
    from = at + needle.length
  }
  return out
}

export function hasMatch(text: string, query: string): boolean {
  if (!query.trim()) return false
  return fold(text).includes(fold(query))
}

export function matchingIndices<T>(
  items: readonly T[],
  segmentsOf: (item: T) => string[],
  query: string,
): number[] {
  if (!query.trim()) return []
  const out: number[] = []
  for (let i = 0; i < items.length; i++) {
    if (segmentsOf(items[i]).some((seg) => hasMatch(seg, query))) out.push(i)
  }
  return out
}
