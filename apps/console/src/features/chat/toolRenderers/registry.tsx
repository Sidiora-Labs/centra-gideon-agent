import { type ReactNode } from 'react'
import type { ToolSegment } from '../chatTypes'
import { ToolOutput } from '../../tools/ToolOutput'
import { RawBlock, KeyValueFields, ContentTypeOutput, inputOf } from './primitives'
import { nativeRendererForTool } from './native'

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
