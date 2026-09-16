export interface PlanStep {
  id: string
  label?: string
  role?: string
  kind?: string
  target?: string
  pending?: boolean
  depends_on?: string[]
}
export interface PlanDraft { title?: string; description?: string; steps: PlanStep[] }

type Container = { end: '}' | ']'; value: boolean }

function completeFragment(source: string): string {
  const containers: Container[] = []
  let quote: { start: number; value: boolean } | null = null
  let escape = false
  for (const [offset, character] of source.split('').entries()) {
    if (quote) {
      if (escape) escape = false
      else if (character === '\\') escape = true
      else if (character === '"') quote = null
      continue
    }
    const current = containers[containers.length - 1]
    switch (character) {
      case '"': quote = { start: offset, value: !current || current.end === ']' || current.value }; break
      case '{': containers.push({ end: '}', value: false }); break
      case '[': containers.push({ end: ']', value: false }); break
      case '}': case ']': containers.pop(); break
      case ':': if (current?.end === '}') current.value = true; break
      case ',': if (current?.end === '}') current.value = false; break
    }
  }
  let tail = quote ? quote.value ? source + '"' : source.slice(0, quote.start) : source
  for (;;) {
    const shortened = tail.trimEnd().replace(/,$/, '').replace(/,?\s*"(?:[^"\\]|\\.)*"\s*:$/, '')
    if (shortened === tail) break
    tail = shortened
  }
  return tail + containers.reverse().map((entry) => entry.end).join('')
}

export function parsePartialJson(buffer: string): unknown {
  const text = buffer.trim()
  if (text.length === 0) return null
  try { return JSON.parse(text) } catch { /* The trailing token may still be arriving. */ }
  try { return JSON.parse(completeFragment(text)) } catch { return null }
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' ? value as Record<string, unknown> : {}
const textField = (...values: unknown[]): string | undefined =>
  values.find((value): value is string => typeof value === 'string' && value.length > 0)

export function toPlanDraft(parsed: unknown, opts: { complete?: boolean } = {}): PlanDraft {
  const root = record(parsed)
  const plan = root.plan && typeof root.plan === 'object' ? record(root.plan) : root
  const rows: unknown[] = Array.isArray(plan.steps) ? plan.steps : Array.isArray(plan.nodes) ? plan.nodes : []
  return {
    title: textField(plan.title), description: textField(plan.description),
    steps: rows.flatMap((row, index): PlanStep[] => {
      if (!row || typeof row !== 'object') return []
      const node = record(row)
      const id = String(node.id ?? node.node_id ?? '').trim()
      if (!id) return []
      return [{
        id, label: textField(node.label, node.title), role: textField(node.role),
        kind: textField(node.kind), target: textField(node.target, node.objective),
        depends_on: Array.isArray(node.depends_on) ? Array.from(node.depends_on, String) : undefined,
        pending: !opts.complete && index + 1 === rows.length,
      }]
    }),
  }
}

export function reparseBuffer(buffer: string, last: PlanDraft | null, opts: { complete?: boolean } = {}): { draft: PlanDraft; parsed: boolean } {
  const value = parsePartialJson(buffer)
  const parsed = value != null
  return { parsed, draft: parsed ? toPlanDraft(value, opts) : last ?? { steps: [] } }
}
