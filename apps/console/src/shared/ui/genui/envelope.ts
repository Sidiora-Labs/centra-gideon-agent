import { genUiV2Catalog } from './registry'

export interface GenUiElementState {
  selected?: string
  filter?: string
  fields?: Record<string, string>
}

export interface GenUiV2Element {
  type: string
  props: Record<string, unknown>
  children?: string[]
}

export interface GenUiV2Envelope {
  schemaVersion: 2
  id: string
  revision: number
  root: string
  elements: Record<string, GenUiV2Element>
  state: Record<string, GenUiElementState>
}

export type GenUiEnvelopeParseResult =
  | { kind: 'legacy' }
  | { kind: 'incomplete'; message: string }
  | { kind: 'invalid'; message: string }
  | { kind: 'v2'; envelope: GenUiV2Envelope }

type JsonObject = Record<string, unknown>
type JsonSchema = {
  $ref?: string
  oneOf?: JsonSchema[]
  not?: JsonSchema
  type?: string
  const?: unknown
  enum?: unknown[]
  properties?: Record<string, JsonSchema>
  propertyNames?: JsonSchema
  required?: string[]
  additionalProperties?: boolean | JsonSchema
  items?: JsonSchema
  uniqueItems?: boolean
  minProperties?: number
  maxProperties?: number
  minLength?: number
  maxLength?: number
  maxItems?: number
  minimum?: number
  pattern?: string
}

const MAX_ENVELOPE_BYTES = 256 * 1024
const MAX_SCHEMA_DEPTH = 40
const MAX_GRAPH_DEPTH = 32

class StrictJsonError extends Error {
  constructor(message: string, readonly incomplete = false) { super(message) }
}

class StrictJsonReader {
  private index = 0
  constructor(private readonly source: string) {}

  parse(): unknown {
    const value = this.value(0)
    this.space()
    if (this.index !== this.source.length) this.fail('Unexpected trailing JSON content.')
    return value
  }

  private fail(message: string, incomplete = this.index >= this.source.length): never {
    throw new StrictJsonError(message, incomplete)
  }

  private space(): void {
    while (/\s/.test(this.source[this.index] ?? '')) this.index++
  }

  private value(depth: number): unknown {
    if (depth > MAX_SCHEMA_DEPTH) this.fail(`JSON nesting exceeds ${MAX_SCHEMA_DEPTH} levels.`)
    this.space()
    const token = this.source[this.index]
    if (token === undefined) this.fail('JSON ended before the value was complete.', true)
    if (token === '{') return this.object(depth + 1)
    if (token === '[') return this.array(depth + 1)
    if (token === '"') return this.string()
    if (token === '-' || /\d/.test(token)) return this.number()
    for (const [word, value] of [['true', true], ['false', false], ['null', null]] as const) {
      if (this.source.startsWith(word, this.index)) { this.index += word.length; return value }
      if (word.startsWith(this.source.slice(this.index))) this.fail(`JSON ended inside ${word}.`, true)
    }
    this.fail(`Unexpected JSON token at character ${this.index + 1}.`)
  }

  private object(depth: number): JsonObject {
    this.index++
    const result: JsonObject = Object.create(null)
    const keys = new Set<string>()
    this.space()
    if (this.source[this.index] === '}') { this.index++; return result }
    while (true) {
      this.space()
      if (this.source[this.index] !== '"') this.fail('An object key must be a JSON string.')
      const key = this.string()
      if (keys.has(key)) this.fail(`Duplicate object key "${key}".`)
      keys.add(key)
      this.space()
      if (this.source[this.index] !== ':') this.fail(`Object key "${key}" is missing a colon.`)
      this.index++
      result[key] = this.value(depth)
      this.space()
      const token = this.source[this.index]
      if (token === '}') { this.index++; return result }
      if (token !== ',') this.fail('An object entry must end with a comma or closing brace.')
      this.index++
    }
  }

  private array(depth: number): unknown[] {
    this.index++
    const result: unknown[] = []
    this.space()
    if (this.source[this.index] === ']') { this.index++; return result }
    while (true) {
      result.push(this.value(depth))
      this.space()
      const token = this.source[this.index]
      if (token === ']') { this.index++; return result }
      if (token !== ',') this.fail('An array item must end with a comma or closing bracket.')
      this.index++
    }
  }

  private string(): string {
    const start = this.index++
    let escaped = false
    while (this.index < this.source.length) {
      const token = this.source[this.index++]
      if (!escaped && token === '"') {
        try { return JSON.parse(this.source.slice(start, this.index)) as string }
        catch { this.fail('Invalid JSON string escape.') }
      }
      if (!escaped && token === '\\') escaped = true
      else escaped = false
    }
    this.fail('JSON ended inside a string.', true)
  }

  private number(): number {
    const match = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?/.exec(this.source.slice(this.index))
    if (!match) this.fail('Invalid JSON number.')
    this.index += match[0].length
    const value = Number(match[0])
    if (!Number.isFinite(value)) this.fail('JSON numbers must be finite.')
    return value
  }
}

const isObject = (value: unknown): value is JsonObject => typeof value === 'object' && value !== null && !Array.isArray(value)
const pathKey = (path: string, key: string) => `${path}.${key}`

function resolveSchema(ref: string): JsonSchema | null {
  const prefix = '#/$defs/'
  if (!ref.startsWith(prefix)) return null
  return (genUiV2Catalog.$defs[ref.slice(prefix.length)] as JsonSchema | undefined) ?? null
}

function schemaError(value: unknown, schema: JsonSchema, path = '$', depth = 0): string | null {
  if (depth > MAX_SCHEMA_DEPTH) return `${path} exceeds the ${MAX_SCHEMA_DEPTH}-level validation limit.`
  if (schema.$ref) {
    const resolved = resolveSchema(schema.$ref)
    return resolved ? schemaError(value, resolved, path, depth + 1) : `${path} uses an unknown schema reference.`
  }
  if (schema.not && schemaError(value, schema.not, path, depth + 1) === null) return `${path} contains a prohibited value.`
  if (schema.const !== undefined && !Object.is(value, schema.const)) return `${path} must equal ${JSON.stringify(schema.const)}.`
  if (schema.enum && !schema.enum.some(option => Object.is(value, option))) return `${path} must be one of ${schema.enum.map(String).join(', ')}.`
  if (schema.oneOf) {
    const errors = schema.oneOf.map(option => schemaError(value, option, path, depth + 1))
    if (errors.filter(error => error === null).length === 1) return null
    if (isObject(value) && typeof value.type === 'string') {
      const matching = schema.oneOf.find(option => option.properties?.type?.const === value.type)
      if (matching) return schemaError(value, matching, path, depth + 1)
      if (schema.oneOf.some(option => option.properties?.type?.const !== undefined)) return `${path}.type "${value.type}" is not in the GenUI v2 catalog.`
    }
    return errors.find((error): error is string => error !== null) ?? `${path} does not match exactly one permitted shape.`
  }

  if (schema.type === 'null' && value !== null) return `${path} must be null.`
  if (schema.type === 'string' && typeof value !== 'string') return `${path} must be a string.`
  if (schema.type === 'boolean' && typeof value !== 'boolean') return `${path} must be a boolean.`
  if (schema.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) return `${path} must be a finite number.`
  if (schema.type === 'integer' && (typeof value !== 'number' || !Number.isInteger(value))) return `${path} must be an integer.`
  if (schema.type === 'array' && !Array.isArray(value)) return `${path} must be an array.`
  if (schema.type === 'object' && !isObject(value)) return `${path} must be an object.`

  if (typeof value === 'number' && schema.minimum !== undefined && value < schema.minimum) return `${path} must be at least ${schema.minimum}.`
  if (typeof value === 'string') {
    if (schema.minLength !== undefined && value.length < schema.minLength) return `${path} is too short.`
    if (schema.maxLength !== undefined && value.length > schema.maxLength) return `${path} exceeds ${schema.maxLength} characters.`
    if (schema.pattern && !new RegExp(schema.pattern).test(value)) return `${path} has an invalid format.`
  }
  if (Array.isArray(value)) {
    if (schema.maxItems !== undefined && value.length > schema.maxItems) return `${path} exceeds ${schema.maxItems} items.`
    if (schema.uniqueItems) {
      const serialized = value.map(item => JSON.stringify(item))
      if (new Set(serialized).size !== serialized.length) return `${path} contains duplicate items.`
    }
    if (schema.items) for (let index = 0; index < value.length; index++) {
      const error = schemaError(value[index], schema.items, `${path}[${index}]`, depth + 1)
      if (error) return error
    }
  }
  if (isObject(value)) {
    const keys = Object.keys(value)
    if (schema.minProperties !== undefined && keys.length < schema.minProperties) return `${path} has too few entries.`
    if (schema.maxProperties !== undefined && keys.length > schema.maxProperties) return `${path} exceeds ${schema.maxProperties} entries.`
    for (const required of schema.required ?? []) if (!Object.hasOwn(value, required)) return `${path} is missing required property "${required}".`
    for (const key of keys) {
      if (schema.propertyNames) {
        const error = schemaError(key, schema.propertyNames, `${path} key "${key}"`, depth + 1)
        if (error) return error
      }
      const property = schema.properties?.[key]
      if (property) {
        const error = schemaError(value[key], property, pathKey(path, key), depth + 1)
        if (error) return error
      } else if (schema.additionalProperties === false) return `${path} has unknown property "${key}".`
      else if (isObject(schema.additionalProperties)) {
        const error = schemaError(value[key], schema.additionalProperties, pathKey(path, key), depth + 1)
        if (error) return error
      }
    }
  }
  return null
}

function graphError(envelope: GenUiV2Envelope): string | null {
  if (!Object.hasOwn(envelope.elements, envelope.root)) return `Root element "${envelope.root}" does not exist.`
  for (const stateId of Object.keys(envelope.state)) if (!Object.hasOwn(envelope.elements, stateId)) return `State refers to unknown element "${stateId}".`
  for (const [id, element] of Object.entries(envelope.elements)) {
    const source = element.props.selectionFrom
    if (source !== undefined && (!Object.hasOwn(envelope.elements, String(source)) || envelope.elements[String(source)].type !== 'Compare')) {
      return `Element "${id}" selectionFrom must name a Compare element.`
    }
  }
  const visited = new Set<string>()
  const active = new Set<string>()
  const visit = (id: string, depth: number): string | null => {
    if (depth > MAX_GRAPH_DEPTH) return `Element graph exceeds ${MAX_GRAPH_DEPTH} levels.`
    if (active.has(id)) return `Element graph contains a cycle at "${id}".`
    if (visited.has(id)) return null
    const element = envelope.elements[id]
    if (!element) return `Element graph refers to missing element "${id}".`
    active.add(id)
    for (const child of element.children ?? []) {
      const error = visit(child, depth + 1)
      if (error) return error
    }
    active.delete(id)
    visited.add(id)
    return null
  }
  const error = visit(envelope.root, 1)
  if (error) return error
  const orphan = Object.keys(envelope.elements).find(id => !visited.has(id))
  return orphan ? `Element "${orphan}" is unreachable from root "${envelope.root}".` : null
}

export function parseGenUiEnvelope(content: string): GenUiEnvelopeParseResult {
  const source = content.trim()
  if (!source.startsWith('{')) return { kind: 'legacy' }
  if (new TextEncoder().encode(source).length > MAX_ENVELOPE_BYTES) {
    return { kind: 'invalid', message: `GenUI v2 input exceeds ${MAX_ENVELOPE_BYTES} bytes.` }
  }
  let value: unknown
  try { value = new StrictJsonReader(source).parse() }
  catch (error) {
    const failure = error as StrictJsonError
    return { kind: failure.incomplete ? 'incomplete' : 'invalid', message: failure.message }
  }
  const error = schemaError(value, genUiV2Catalog as unknown as JsonSchema)
  if (error) return { kind: 'invalid', message: error }
  const envelope = value as GenUiV2Envelope
  const graph = graphError(envelope)
  return graph ? { kind: 'invalid', message: graph } : { kind: 'v2', envelope }
}
