export function rowSubject(parts: (string | null | undefined)[], cap = 55): string {
  const seen: string[] = []
  for (const raw of parts) {
    const part = (raw ?? '').replace(/\s+/g, ' ').trim()
    if (!part || seen.some((s) => s === part || s.startsWith(part) || part.startsWith(s))) continue
    seen.push(part)
  }
  const full = seen.join(' — ')
  return full.length > cap ? `${full.slice(0, cap - 1)}…` : full
}
