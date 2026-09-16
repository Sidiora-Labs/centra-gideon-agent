export function previewText(md: string | null | undefined, cap?: number): string {
  let s = md ?? ''
  s = s.replace(/^```[^\n]*$/gm, '')
  s = s.replace(/^\s{0,3}#{1,6}\s+/gm, '')
  s = s.replace(/^\s{0,3}>\s?/gm, '')
  s = s.replace(/^\s{0,3}(?:[-*+]|\d+[.)])\s+/gm, '')
  s = s.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
  s = s.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
  s = s.replace(/\*\*([^*]+)\*\*/g, '$1')
  s = s.replace(/__([^_]+)__/g, '$1')
  s = s.replace(/\*([^*\n]+)\*/g, '$1')
  s = s.replace(/(^|\W)_([^_\n]+)_(?=\W|$)/g, '$1$2')
  s = s.replace(/`([^`]+)`/g, '$1')
  s = s.replace(/\s+/g, ' ').trim()
  return cap && s.length > cap ? `${s.slice(0, cap - 1)}…` : s
}
