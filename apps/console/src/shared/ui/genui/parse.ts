export interface ParsedLine {
  id: string
  component: string
  args: Record<string, unknown>
  argKeys: string[]
  refs: Record<string, string[]>
  line: number
}
export interface ParsedProgram {
  lines: ParsedLine[]
  parseErrors: { line: number; text: string; message: string }[]
}

const identifier = /^[A-Za-z_][A-Za-z0-9_-]*$/
const componentName = /^[A-Za-z_][A-Za-z0-9_]*$/
const matching: Record<string, string> = { '[': ']', '(': ')', '{': '}' }

function segments(text: string): string[] {
  const pieces: string[] = []
  const stack: string[] = []
  let quote = ''
  let escaped = false
  let start = 0
  for (let index = 0; index < text.length; index++) {
    const character = text[index]
    if (quote) {
      if (escaped) escaped = false
      else if (character === '\\') escaped = true
      else if (character === quote) quote = ''
      continue
    }
    if (character === '"' || character === "'") quote = character
    else if (matching[character]) stack.push(matching[character])
    else if (')]}'.includes(character)) {
      if (stack.pop() !== character) throw new Error('unbalanced value delimiters')
    } else if (character === ',' && !stack.length) {
      pieces.push(text.slice(start, index).trim())
      start = index + 1
    }
  }
  if (quote || stack.length) throw new Error('incomplete quoted or nested value')
  if (text.slice(start).trim()) pieces.push(text.slice(start).trim())
  return pieces
}

function literal(text: string): unknown {
  if (!text) return ''
  if (text[0] === '[' && text.at(-1) === ']') return segments(text.slice(1, -1)).map(literal)
  if ((text[0] === '"' || text[0] === "'") && text.at(-1) === text[0]) return text.slice(1, -1).replace(/\\(["'])/g, '$1')
  if (text === 'true' || text === 'false') return text === 'true'
  const number = Number(text)
  return Number.isNaN(number) ? text : number
}

function argument(text: string): { value: unknown } | { references: string[] } {
  const raw = text.trim()
  if (raw.startsWith('[') && raw.endsWith(']')) {
    const elements = segments(raw.slice(1, -1))
    if (elements.length && elements.every(value => identifier.test(value) && value !== 'true' && value !== 'false')) return { references: elements }
    return { value: elements.map(literal) }
  }
  return identifier.test(raw) && raw !== 'true' && raw !== 'false' ? { references: [raw] } : { value: literal(raw) }
}

function invocation(text: string, line: number): ParsedLine {
  const equals = text.indexOf('=')
  const open = text.indexOf('(', equals + 1)
  const id = text.slice(0, equals).trim()
  const component = text.slice(equals + 1, open).trim()
  if (equals < 0 || open < 0 || !identifier.test(id) || !componentName.test(component) || !text.endsWith(')')) throw new Error('expected `id = Component(args…)`')
  const parsed: ParsedLine = { id, component, args: Object.create(null), refs: Object.create(null), argKeys: [], line }
  for (const piece of segments(text.slice(open + 1, -1))) {
    const colon = piece.indexOf(':')
    if (colon < 0) continue
    const key = piece.slice(0, colon).trim()
    if (!key) continue
    const value = argument(piece.slice(colon + 1))
    parsed.argKeys.push(key)
    delete parsed.args[key]
    delete parsed.refs[key]
    if ('references' in value) parsed.refs[key] = value.references
    else parsed.args[key] = value.value
  }
  return parsed
}

export function parseGenUi(body: string): ParsedProgram {
  const program: ParsedProgram = { lines: [], parseErrors: [] }
  body.split('\n').forEach((source, index) => {
    const text = source.trim()
    if (!text || text.startsWith('#') || text.startsWith('//')) return
    try { program.lines.push(invocation(text, index + 1)) }
    catch (error) { program.parseErrors.push({ line: index + 1, text, message: (error as Error).message }) }
  })
  return program
}
