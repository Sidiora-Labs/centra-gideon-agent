
export const HEX = /#[0-9a-fA-F]{3,8}\b/

export const RAW_PX = /style=\{\{[^}]*?\b\d+px\b/

export const PX_OK_CONTEXT = /minmax\(|repeat\(|\bmin\(|\bmax\(|\bclamp\(|\b(border|outline)(-[a-z]+)?:\s*[^;}]*\d+px|border[A-Z][a-zA-Z]*:\s*[`'"]?\s*\$?\{?[^}]*\d+px|Math\.(min|max)\(/

export const CALC_WITH_TOKEN = /calc\([^)]*var\(--(?!content-width\))/

export interface TokenLintState {
  blockComment: boolean
}

export function stripTokenComments(line: string, state: TokenLintState): string {
  let out = ''
  let quote = ''
  for (let i = 0; i < line.length; i++) {
    const ch = line[i]
    const next = line[i + 1]
    if (state.blockComment) {
      out += ' '
      if (ch === '*' && next === '/') {
        state.blockComment = false
        out += ' '
        i++
      }
    } else if (quote) {
      out += ch
      if (ch === '\\' && next !== undefined) {
        out += next
        i++
      } else if (ch === quote) quote = ''
    } else if (ch === '/' && next === '*' && (i === 0 || !/[A-Za-z0-9\[]/.test(line[i - 1]))) {
      state.blockComment = true
      out += '  '
      i++
    } else if (ch === '/' && next === '/' && (i === 0 || line[i - 1] !== ':')) {
      break
    } else {
      out += ch
      if (ch === "'" || ch === '"' || ch === '`') quote = ch
    }
  }
  return out
}

export function lineViolations(line: string, state: TokenLintState = { blockComment: false }): ('hex' | 'px')[] {
  line = stripTokenComments(line, state)
  const out: ('hex' | 'px')[] = []
  if (HEX.test(line)) out.push('hex')
  if (RAW_PX.test(line) && !CALC_WITH_TOKEN.test(line) && !PX_OK_CONTEXT.test(line)) out.push('px')
  return out
}

export interface TokenLintResult {
  violations: string[]
  end_state: 'code' | 'block_comment'
}

export function scanTokenSource(text: string): TokenLintResult {
  const state: TokenLintState = { blockComment: false }
  const hits: string[] = []
  text.split('\n').forEach((line, i) => {
    for (const kind of lineViolations(line, state)) {
      hits.push(`${i + 1}: ${kind} — ${line.trim().slice(0, 80)}`)
    }
  })
  const end_state = state.blockComment ? 'block_comment' : 'code'
  if (end_state !== 'code') {
    hits.push(`${text.split('\n').length}: lexical — unreadable EOF: ${end_state}`)
  }
  return { violations: hits, end_state }
}

export function sourceViolations(text: string): string[] {
  return scanTokenSource(text).violations
}
