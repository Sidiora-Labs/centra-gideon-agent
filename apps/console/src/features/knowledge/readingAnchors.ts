
export const MARK_CLASS = 'kl-highlight'

export const MARK_ID_ATTR = 'data-annotation-id'

export interface ReadingAnchor {
  id: string
  quote: string
  occurrence: number
}

interface FlatText {
  nodes: Text[]
  starts: number[]
  text: string
}

function flatten(root: HTMLElement): FlatText {
  const nodes: Text[] = []
  const starts: number[] = []
  let text = ''
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node: Node) {
      const parent = (node as Text).parentElement
      if (!parent) return NodeFilter.FILTER_REJECT
      const tag = parent.tagName
      if (tag === 'SCRIPT' || tag === 'STYLE') return NodeFilter.FILTER_REJECT
      return NodeFilter.FILTER_ACCEPT
    },
  })
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const t = n as Text
    nodes.push(t)
    starts.push(text.length)
    text += t.data
  }
  return { nodes, starts, text }
}

function absoluteIndex(flat: FlatText, container: Node, offset: number): number {
  if (container.nodeType === Node.TEXT_NODE) {
    const i = flat.nodes.indexOf(container as Text)
    return i === -1 ? -1 : flat.starts[i] + offset
  }
  const child = container.childNodes[offset]
  if (!child) {
    const inside = flat.nodes.filter((n) => container.contains(n))
    if (!inside.length) return -1
    const last = inside[inside.length - 1]
    return flat.starts[flat.nodes.indexOf(last)] + last.data.length
  }
  const next = flat.nodes.find((n) => n === child || child.contains(n))
  return next ? flat.starts[flat.nodes.indexOf(next)] : -1
}

function occurrenceBefore(text: string, quote: string, before: number): number {
  let n = 0
  for (let i = text.indexOf(quote); i !== -1 && i < before; i = text.indexOf(quote, i + 1)) n += 1
  return n
}

function nthIndexOf(text: string, quote: string, occurrence: number): number {
  let i = text.indexOf(quote)
  for (let n = 0; i !== -1 && n < occurrence; n += 1) i = text.indexOf(quote, i + 1)
  return i
}

export function anchorFromSelection(
  root: HTMLElement,
  selection: Selection | null,
): { quote: string; occurrence: number } | null {
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null
  const range = selection.getRangeAt(0)
  if (!root.contains(range.commonAncestorContainer)) return null
  const flat = flatten(root)
  if (!flat.nodes.length) return null
  const start = absoluteIndex(flat, range.startContainer, range.startOffset)
  const end = absoluteIndex(flat, range.endContainer, range.endOffset)
  if (start < 0 || end < 0 || end <= start) return null
  const quote = flat.text.slice(start, end).trim()
  if (!quote) return null
  const at = flat.text.indexOf(quote, start)
  if (at === -1) return null
  return { quote, occurrence: occurrenceBefore(flat.text, quote, at) }
}

export function clearMarks(root: HTMLElement): void {
  const marks = Array.from(root.querySelectorAll(`.${MARK_CLASS}`))
  for (const mark of marks) {
    const parent = mark.parentNode
    if (!parent) continue
    while (mark.firstChild) parent.insertBefore(mark.firstChild, mark)
    parent.removeChild(mark)
  }
  if (marks.length) root.normalize()
}

export function markAnchors(root: HTMLElement, anchors: ReadingAnchor[]): string[] {
  clearMarks(root)
  const unresolved: string[] = []
  for (const anchor of anchors) {
    const flat = flatten(root)
    const at = nthIndexOf(flat.text, anchor.quote, anchor.occurrence)
    if (at === -1 || !anchor.quote) {
      unresolved.push(anchor.id)
      continue
    }
    paint(flat, at, at + anchor.quote.length, anchor.id)
  }
  return unresolved
}

function paint(flat: FlatText, start: number, end: number, id: string): void {
  for (let i = flat.nodes.length - 1; i >= 0; i -= 1) {
    const node = flat.nodes[i]
    const nodeStart = flat.starts[i]
    const nodeEnd = nodeStart + node.data.length
    const from = Math.max(start, nodeStart)
    const to = Math.min(end, nodeEnd)
    if (to <= from) continue
    let target = node
    if (to < nodeEnd) target.splitText(to - nodeStart)
    if (from > nodeStart) target = target.splitText(from - nodeStart)
    const mark = node.ownerDocument.createElement('mark')
    mark.className = MARK_CLASS
    mark.setAttribute(MARK_ID_ATTR, id)
    target.parentNode?.insertBefore(mark, target)
    mark.appendChild(target)
  }
}

export function scrollProgress(el: {
  scrollTop: number
  scrollHeight: number
  clientHeight: number
}): number {
  const scrollable = el.scrollHeight - el.clientHeight
  if (scrollable <= 0) return 1
  return Math.max(0, Math.min(1, el.scrollTop / scrollable))
}
