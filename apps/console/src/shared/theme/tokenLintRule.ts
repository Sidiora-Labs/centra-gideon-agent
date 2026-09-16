
export const HEX = /#[0-9a-fA-F]{3,8}\b/

export const RAW_PX = /style=\{\{[^}]*?\b\d+px\b/

export const PX_OK_CONTEXT = /minmax\(|repeat\(|\bmin\(|\bmax\(|\bclamp\(|\b(border|outline)(-[a-z]+)?:\s*[^;}]*\d+px|border[A-Z][a-zA-Z]*:\s*[`'"]?\s*\$?\{?[^}]*\d+px|Math\.(min|max)\(/

export const CALC_WITH_TOKEN = /calc\([^)]*var\(/

export function lineViolations(line: string): ('hex' | 'px')[] {
  const out: ('hex' | 'px')[] = []
  if (HEX.test(line)) out.push('hex')
  if (RAW_PX.test(line) && !CALC_WITH_TOKEN.test(line) && !PX_OK_CONTEXT.test(line)) out.push('px')
  return out
}
