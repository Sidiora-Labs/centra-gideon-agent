export function preflightRemediations(detail: unknown): string[] {
  if (!detail || typeof detail !== 'object') return []
  const preflight = (detail as Record<string, unknown>).preflight
  if (!preflight || typeof preflight !== 'object') return []
  const findings = (preflight as Record<string, unknown>).findings
  if (!Array.isArray(findings)) return []
  const out: string[] = []
  const seen = new Set<string>()
  for (const f of findings) {
    if (!f || typeof f !== 'object') continue
    const r = (f as Record<string, unknown>).remediation
    if (typeof r !== 'string' || !r.trim()) continue
    const kind = (f as Record<string, unknown>).kind
    const key = typeof kind === 'string' && kind.trim() ? `kind:${kind.trim()}` : `text:${r.trim()}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push(r.trim())
  }
  return out
}
