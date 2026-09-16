
function tokenize(path: string): string[] {
  return path.split(/(\d+)/)
}

export function compareInstancePaths(left: string, right: string): number {
  const a = tokenize(left)
  const b = tokenize(right)
  const shared = Math.min(a.length, b.length)

  for (let i = 0; i < shared; i += 1) {
    if (a[i] === b[i]) continue
    if (i % 2 === 1) {
      const na = Number(a[i])
      const nb = Number(b[i])
      if (na !== nb) return na < nb ? -1 : 1
      continue
    }
    return a[i] < b[i] ? -1 : 1
  }

  if (a.length !== b.length) return a.length < b.length ? -1 : 1
  return 0
}

export function byInstancePath(
  a: { instance_path: string },
  b: { instance_path: string },
): number {
  return compareInstancePaths(a.instance_path, b.instance_path)
}
