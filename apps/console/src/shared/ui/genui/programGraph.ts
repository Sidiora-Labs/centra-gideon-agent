import type { ParsedProgram } from './parse'

export function planGenUiProgram(program: ParsedProgram) {
  const byId = new Map(program.lines.map(line => [line.id, line]))
  const referenced = new Set([...byId.values()].flatMap(line => Object.values(line.refs).flat()))
  const roots = [...byId.values()].filter(line => !referenced.has(line.id))
  const reached = new Set<string>()
  const include = (root: string) => {
    const pending = [root]
    while (pending.length) {
      const id = pending.pop()!
      if (reached.has(id)) continue
      reached.add(id)
      const line = byId.get(id)
      if (line) pending.push(...Object.values(line.refs).flat())
    }
  }
  roots.forEach(root => include(root.id))
  for (const line of byId.values()) {
    if (reached.has(line.id)) continue
    roots.push(line)
    include(line.id)
  }
  return { byId, roots, parseErrors: program.parseErrors }
}
