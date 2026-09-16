
export function pyMethod(src: string, header: string): string {
  const start = src.indexOf(header)
  if (start < 0) return ''
  const indent = (header.match(/^\s*/) ?? [''])[0]
  const rest = src.slice(start + header.length)
  const next = rest.search(new RegExp(`\\n${indent}(async )?def `))
  return next < 0 ? rest : rest.slice(0, next)
}

export function pyBetween(src: string, from: string, to: string): string {
  const a = src.indexOf(from)
  if (a < 0) return ''
  const b = src.indexOf(to, a + from.length)
  return b < 0 ? '' : src.slice(a, b)
}
