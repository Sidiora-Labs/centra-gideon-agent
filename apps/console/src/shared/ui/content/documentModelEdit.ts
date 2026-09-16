import type { DocumentBlock, DocumentModelJson, DocumentPageSetup, DocumentParagraphStyle, DocumentRun } from '../../data/api'

export type RunMark = 'bold' | 'italic' | 'code'
const plainRun: DocumentRun = { text: '', bold: false, italic: false, code: false, link: '' }
export function blockRuns(block: DocumentBlock): DocumentRun[] {
  return block.runs.length ? block.runs : block.text ? [{ ...plainRun, text: block.text }] : []
}
export function blockText(block: DocumentBlock): string { return blockRuns(block).map(run => run.text).join('') }

function runRanges(block: DocumentBlock) {
  let offset = 0
  return blockRuns(block).map(run => {
    const start = offset
    offset += run.text.length
    return { run, start, end: offset }
  })
}
export function selectionHasMark(block: DocumentBlock, start: number, end: number, mark: RunMark): boolean {
  if (end <= start) return false
  const touched = runRanges(block).filter(range => range.end > start && range.start < end)
  return touched.length > 0 && touched.every(({ run }) => run[mark])
}
export function mergeRuns(runs: DocumentRun[]): DocumentRun[] {
  const groups: DocumentRun[] = []
  const marks = ['bold', 'italic', 'code', 'link'] as const
  for (const run of runs.filter(run => run.text.length > 0)) {
    const previous = groups.at(-1)
    if (previous && marks.every(mark => previous[mark] === run[mark])) groups[groups.length - 1] = { ...previous, text: previous.text + run.text }
    else groups.push(run)
  }
  return groups
}
export function applyMark(block: DocumentBlock, start: number, end: number, mark: RunMark, on: boolean): DocumentBlock {
  if (end <= start) return block
  const pieces = runRanges(block).flatMap(range => {
    if (range.end <= start || range.start >= end) return [range.run]
    const inside = [Math.max(range.start, start) - range.start, Math.min(range.end, end) - range.start]
    const boundaries = [0, ...inside, range.run.text.length]
    return boundaries.slice(1).flatMap((right, index) => {
      const left = boundaries[index]
      if (right <= left) return []
      return [{ ...range.run, text: range.run.text.slice(left, right), ...(index === 1 ? { [mark]: on } : {}) }]
    })
  })
  return { ...block, runs: mergeRuns(pieces), text: '' }
}

function replacementSpan(before: string, after: string) {
  let begin = 0
  for (; begin < Math.min(before.length, after.length) && before[begin] === after[begin]; begin++) {}
  let oldEnd = before.length
  let newEnd = after.length
  for (; oldEnd > begin && newEnd > begin && before[oldEnd - 1] === after[newEnd - 1]; oldEnd--, newEnd--) {}
  return { begin, oldEnd, inserted: after.slice(begin, newEnd) }
}
export function setBlockText(block: DocumentBlock, text: string): DocumentBlock {
  const ranges = runRanges(block)
  const before = ranges.map(({ run }) => run.text).join('')
  if (before === text) return block
  const change = replacementSpan(before, text)
  const owner = ranges.find(range => range.start <= change.begin && change.oldEnd <= range.end)
  let runs: DocumentRun[]
  if (ranges.length < 2 || !owner) runs = text ? [{ ...(ranges[0]?.run ?? plainRun), text }] : []
  else runs = ranges.map(range => range !== owner ? range.run : {
    ...range.run,
    text: range.run.text.slice(0, change.begin - range.start) + change.inserted + range.run.text.slice(change.oldEnd - range.start),
  })
  return { ...block, runs: mergeRuns(runs), text: '' }
}
export function withBlock(model: DocumentModelJson, index: number, block: DocumentBlock): DocumentModelJson {
  return { ...model, blocks: Array.from(model.blocks, (entry, position) => position === index ? block : entry) }
}

export const EMPTY_PAGE: DocumentPageSetup = {
  size: '', orientation: '', margin_top_pt: 0, margin_bottom_pt: 0, margin_left_pt: 0, margin_right_pt: 0,
  header_text: '', footer_text: '', page_numbers: false,
}
export const EMPTY_STYLE: DocumentParagraphStyle = {
  align: '', space_before_pt: 0, space_after_pt: 0, line_spacing: 0,
  indent_left_pt: 0, indent_right_pt: 0, first_line_indent_pt: 0, keep_with_next: false,
}
export function pageOf(model: DocumentModelJson): DocumentPageSetup { return model.page ?? EMPTY_PAGE }
export function styleOf(block: DocumentBlock): DocumentParagraphStyle { return block.style ?? EMPTY_STYLE }
export function withPage(model: DocumentModelJson, patch: Partial<DocumentPageSetup>): DocumentModelJson {
  return Object.assign({}, model, { page: Object.assign({}, pageOf(model), patch) })
}
export function withStyle(model: DocumentModelJson, index: number, patch: Partial<DocumentParagraphStyle>): DocumentModelJson {
  const block = model.blocks[index]
  return block ? withBlock(model, index, { ...block, style: Object.assign({}, styleOf(block), patch) }) : model
}
export const TEXT_BLOCK_KINDS: ReadonlyArray<DocumentBlock['kind']> = ['heading', 'paragraph', 'code']
export function isTextBlock(block: DocumentBlock): boolean { return TEXT_BLOCK_KINDS.some(kind => kind === block.kind) }
