import { type ReactNode } from 'react'
import type { ToolSegment } from '../chatTypes'
import { ToolOutput } from '../../tools/ToolOutput'
import { RawBlock, KeyValueFields, ContentTypeOutput, inputOf } from './primitives'
import { nativeRendererForTool } from './native'
import { connectedUISpecContracts, renderAuiResult } from '../auiResultRegistry'
import { CodeDiff, type DiffLine } from '../../../shared/vendor/assistant-ui/elements/code-diff'
import { TerminalBlock } from '../../../shared/vendor/assistant-ui/elements/terminal-block'

export interface ToolRenderer {
  label: string
  inputLabel?: string
  input?: (seg: ToolSegment) => ReactNode
  output?: (seg: ToolSegment) => ReactNode
}

function findNative(seg: ToolSegment): ToolRenderer | undefined {
  return nativeRendererForTool(seg.tool)
}

export function renderToolInput(seg: ToolSegment): ReactNode {
  const obj = inputOf(seg)
  const effective: ToolSegment = obj && !seg.inputObj ? { ...seg, inputObj: obj } : seg
  const native = findNative(effective)
  if (native?.input) {
    const node = safe(() => native.input!(effective))
    if (node !== undefined) return node
  }
  if (obj && native?.inputLabel) {
    const node = safe(() => <KeyValueFields obj={obj} label={native.inputLabel} />)
    if (node !== undefined) return node
  }
  if (obj) {
    const node = safe(() => <KeyValueFields obj={obj} />)
    if (node !== undefined) return node
  }
  if (seg.input) return <RawBlock label="Input">{seg.input}</RawBlock>
  return null
}

export function renderToolOutput(seg: ToolSegment): ReactNode {
  if (seg.output == null || seg.output === '') return null
  const native = findNative(seg)
  if (native?.output) {
    const node = safe(() => native.output!(seg))
    if (node !== undefined) return node
  }
  const structured = safe(() => renderAuiResult(seg, connectedUISpecContracts))
  if (structured) return structured
  if (seg.done && seg.ok !== false && !seg.truncated) {
    const diff = singleFileDiff(seg.output)
    if (diff) return <CodeDiff {...diff} cycle={0} />
  }
  const command = shellCommand(seg)
  if (command && seg.ok !== false && !seg.truncated) {
    const lines = seg.output.split('\n')
    if (lines.at(-1) === '') lines.pop()
    return <TerminalBlock command={command} lines={lines} visibleCount={lines.length} done={seg.done} />
  }
  const ct = (seg.contentType && seg.contentType !== 'generic')
    ? seg.contentType
    : sniffContentType(seg.output)
  if (ct && ct !== 'generic') {
    const eff: ToolSegment = ct === seg.contentType ? seg : { ...seg, contentType: ct }
    const node = safe(() => <ContentTypeOutput seg={eff} />)
    if (node !== undefined) return node
  }
  const node = safe(() => <RawBlock label="Result"><ToolOutput text={seg.output!} /></RawBlock>)
  if (node !== undefined) return node
  return <RawBlock label="Result">{seg.output}</RawBlock>
}

function shellCommand(seg: ToolSegment): string | null {
  const name = seg.tool.split('__').at(-1)?.toLowerCase()
  if (!name || !['bash', 'shell', 'terminal', 'exec', 'exec_command', 'run_command', 'execute_command'].includes(name)) return null
  const input = inputOf(seg)
  const value = input ? (input.command ?? input.cmd) : seg.input
  if (!input && typeof value === 'string' && /^[\[{]/.test(value.trim())) return null
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function singleFileDiff(output: string): { filename: string; additions: number; deletions: number; lines: DiffLine[] } | null {
  const source = output.replace(/\n$/, '').split('\n')
  let pos = 0
  let gitPath: string | null = null
  const git = /^diff --git a\/(\S+) b\/(\S+)$/.exec(source[pos])
  if (git) {
    if (git[1] !== git[2]) return null
    gitPath = git[2]
    pos++
    while (pos < source.length && /^(index |new file mode |deleted file mode |old mode |new mode )/.test(source[pos])) pos++
  }
  const oldHeader = /^--- (?:a\/(\S+)|\/dev\/null)$/.exec(source[pos++] ?? '')
  const newHeader = /^\+\+\+ (?:b\/(\S+)|\/dev\/null)$/.exec(source[pos++] ?? '')
  if (!oldHeader || !newHeader) return null
  const oldPath = oldHeader[1]
  const newPath = newHeader[1]
  if ((!oldPath && !newPath) || (oldPath && newPath && oldPath !== newPath)) return null
  const filename = newPath ?? oldPath
  if (!filename || (gitPath && gitPath !== filename)) return null
  const lines: DiffLine[] = []
  let additions = 0
  let deletions = 0
  let hunks = 0
  while (pos < source.length) {
    const hunk = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$/.exec(source[pos++])
    if (!hunk) return null
    hunks++
    let oldLeft = hunk[2] === undefined ? 1 : Number(hunk[2])
    let newLeft = hunk[4] === undefined ? 1 : Number(hunk[4])
    while (pos < source.length && !source[pos].startsWith('@@ ')) {
      const line = source[pos++]
      if (line === '\\ No newline at end of file') continue
      if (line.startsWith('+')) { additions++; newLeft--; lines.push({ kind: 'added', text: line.slice(1) }) }
      else if (line.startsWith('-')) { deletions++; oldLeft--; lines.push({ kind: 'removed', text: line.slice(1) }) }
      else if (line.startsWith(' ')) { oldLeft--; newLeft--; lines.push({ kind: 'context', text: line.slice(1) }) }
      else return null
      if (oldLeft < 0 || newLeft < 0) return null
    }
    if (oldLeft !== 0 || newLeft !== 0) return null
  }
  return hunks ? { filename, additions, deletions, lines } : null
}

const _DIFF_RE = /^(diff --git |@@ -\d|index [0-9a-f]+\.\.|\+\+\+ |--- )/m
const _TEST_RE = /\b(PASSED|FAILED|\d+ passed|\d+ failed|=+ test session|FAIL\b|AssertionError)\b/
const _CODE_RE = /^\s*(def |class |import |from \w+ import |function |const |let |var |pub fn |fn |func |interface |type \w+ (struct|interface)|package |#include |public |private |protected )/gm
export function sniffContentType(text: string | undefined): string {
  const s = (text ?? '').slice(0, 4096)
  if (!s.trim()) return 'generic'
  if (_DIFF_RE.test(s)) return 'diff'
  if (_TEST_RE.test(s)) return 'test'
  if (/^\s*[[{]/.test(s)) {
    try { JSON.parse((text ?? '').trim()); return 'json' } catch {   }
  }
  if (looksCsv(s)) return 'csv'
  if (looksCode(s)) return 'code'
  return 'generic'
}
function looksCode(sample: string): boolean {
  if (sample.startsWith('#!')) return true
  const lines = sample.split('\n').filter((l) => l.trim())
  if (lines.length < 8) return false
  const hits = (sample.match(_CODE_RE) || []).length
  return hits >= 3 && hits / lines.length >= 0.05
}
function looksCsv(sample: string): boolean {
  const lines = sample.split('\n').filter((l) => l.trim()).slice(0, 5)
  if (lines.length < 2) return false
  const counts = lines.map((l) => (l.match(/,/g) || []).length)
  return counts[0] >= 1 && new Set(counts).size === 1
}

function safe(fn: () => ReactNode): ReactNode | undefined {
  try {
    return fn()
  } catch {
    return undefined
  }
}

export { iconForTool, labelForTool } from './native'
export { inputOf, resolveInputObj } from './primitives'
