export const CSS_VALUE_ALLOWED_RE = /^[a-zA-Z0-9#(),.\- %/]+$/
export const CSS_DANGEROUS_FUNC_RE = /url\s*\(|expression\s*\(|image\s*\(|image-set\s*\(|paint\s*\(|element\s*\(/i
export const CSS_VALUE_MAX_LEN = 200

const valueRules = [
  (value: string) => value.length > 0,
  (value: string) => CSS_VALUE_ALLOWED_RE.test(value),
  (value: string) => !CSS_DANGEROUS_FUNC_RE.test(value),
]

export function sanitizeCssValue(value: unknown): string {
  if (typeof value !== 'string' || value.length > CSS_VALUE_MAX_LEN) return ''
  const candidate = value.trim()
  return valueRules.every(accept => accept(candidate)) ? candidate : ''
}
