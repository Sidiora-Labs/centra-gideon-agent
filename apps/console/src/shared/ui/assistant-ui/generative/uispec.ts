import { donorStructures } from './donorStructures'

export const generativeActions = {
  stays: ['book_stay'], booking: ['book_reservation'],
  'order-status': ['view_order_event', 'track_order', 'contact_support'],
  'flight-tracker': [], portfolio: [], 'create-event': ['add_to_calendar'],
  'view-event': [], 'weather-current': [], 'ride-status': [],
  'draft-email': ['send_email', 'discard_email'], cart: ['add_to_cart', 'purchase_cart'],
  playlist: ['play_track', 'view_playlist'], 'channel-message': [],
  receipt: ['view_order_details'], 'chart-area': [], 'player-card': [],
  'event-session': ['view_speaker'], 'notify-confirm': ['cancel_delete', 'confirm_delete'],
  'chart-line': [], 'create-task': ['create_task'],
  'software-purchase': ['confirm_purchase'], 'chart-bars': [],
} as const
export const firstGenerativeTemplates = Object.fromEntries(Object.entries(generativeActions).slice(0, 6)) as
  Pick<typeof generativeActions, 'stays' | 'booking' | 'order-status' | 'flight-tracker' | 'portfolio' | 'create-event'>
export type GenerativeTemplate = keyof typeof generativeActions
export type UISpecNode = {
  $type: string
  $key?: string
  $action?: { type: string }
  children?: UISpecNode | UISpecNode[]
  [property: string]: unknown
}
export type LiveUISpec = {
  template: GenerativeTemplate
  producer: string
  recordId: string
  tree: UISpecNode
}
export type BoundUISpec = Omit<LiveUISpec, 'tree'> & { bindings: Record<string, unknown> }

const allowedTypes = new Set([
  'Alert', 'Badge', 'Box', 'Button', 'Caption', 'Card', 'Chart', 'Checkbox',
  'Col', 'DatePicker', 'Divider', 'Fact', 'Form', 'Header', 'Icon', 'Image',
  'Input', 'ListView', 'ListViewItem', 'Markdown', 'RadioGroup', 'Row',
  'Select', 'Spacer', 'Table', 'Text',
])
const MAX_NODES = 256
const MAX_DEPTH = 16
const MAX_STRING = 4096

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function safeString(value: unknown, max = MAX_STRING): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= max
}
function validValue(value: unknown, depth: number): boolean {
  if (depth > 4) return false
  if (value === null || typeof value === 'boolean') return true
  if (typeof value === 'number') return Number.isFinite(value)
  if (typeof value === 'string') return value.length <= MAX_STRING
  if (Array.isArray(value)) return value.length <= 128 && value.every((item) => validValue(item, depth + 1))
  return object(value) && Object.keys(value).length <= 32 &&
    Object.entries(value).every(([key, item]) => key !== '__proto__' && validValue(item, depth + 1))
}
function templateOf(value: unknown): GenerativeTemplate | null {
  return safeString(value, 48) && Object.hasOwn(generativeActions, value) ? value as GenerativeTemplate : null
}
function envelope(input: unknown): { template: GenerativeTemplate; producer: string; recordId: string } | null {
  if (!object(input)) return null
  const template = templateOf(input.template)
  return template && safeString(input.producer, 128) && safeString(input.recordId, 128)
    ? { template, producer: input.producer, recordId: input.recordId } : null
}
function validAction(value: unknown, actions: readonly string[]): boolean {
  return object(value) && safeString(value.type, 64) && actions.includes(value.type) &&
    Object.keys(value).every((key) => key === 'type')
}

export function parseLiveUISpec(input: unknown): LiveUISpec | null {
  const identity = envelope(input)
  if (!identity || !object(input)) return null
  const actions: readonly string[] = generativeActions[identity.template]
  let count = 0
  function visit(value: unknown, depth: number): value is UISpecNode {
    if (!object(value) || depth > MAX_DEPTH || ++count > MAX_NODES ||
        !safeString(value.$type, 32) || !allowedTypes.has(value.$type)) return false
    if (value.$key !== undefined && !safeString(value.$key, 128)) return false
    if (value.$action !== undefined && !validAction(value.$action, actions)) return false
    for (const [key, item] of Object.entries(value)) {
      if (key === 'children') {
        const children = Array.isArray(item) ? item : [item]
        const keys = new Set<string>()
        for (const child of children) {
          if (!visit(child, depth + 1)) return false
          if (child.$key) {
            if (keys.has(child.$key)) return false
            keys.add(child.$key)
          }
        }
      } else if (key === 'confirm' || key === 'cancel' || (key === 'submit' && object(item))) {
        if (!object(item) || !validValue(item, 0) ||
            (item.$action !== undefined && !validAction(item.$action, actions))) return false
      } else if (key !== '$type' && key !== '$key' && key !== '$action' &&
                 (key.startsWith('$') || key.startsWith('on') || !validValue(item, 0))) return false
    }
    return true
  }
  if (!visit(input.tree, 0)) return null
  return { ...identity, tree: input.tree }
}

export function requiredBindings(template: GenerativeTemplate): string[] {
  const definition = donorStructures.find((item) => item.slug === template)
  if (!definition) return []
  const keys: string[] = []
  function collect(value: unknown): void {
    if (Array.isArray(value)) { value.forEach(collect); return }
    if (!object(value)) return
    if (safeString(value.$bind) && safeString(value.$kind)) { keys.push(value.$bind); return }
    Object.values(value).forEach(collect)
  }
  collect(definition.tree)
  return keys
}

export function bindDonorUISpec(input: unknown): LiveUISpec | null {
  const identity = envelope(input)
  if (!identity || !object(input) || !object(input.bindings)) return null
  const definition = donorStructures.find((item) => item.slug === identity.template)!
  const required = requiredBindings(identity.template)
  const allowed = new Set(required)
  const bindings = input.bindings
  if (Object.keys(bindings).length !== allowed.size ||
      !Object.keys(bindings).every((key) => allowed.has(key))) return null
  let valid = true
  function fill(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(fill)
    if (!object(value)) return value
    if (safeString(value.$bind) && safeString(value.$kind)) {
      const bound = bindings[value.$bind]
      const kind = Array.isArray(bound) ? 'array' : bound === null ? 'null' : typeof bound
      if (!Object.hasOwn(bindings, value.$bind) || !validValue(bound, 0) || kind !== value.$kind ||
          ((value.$bind.endsWith('.src') || value.$bind === 'src') &&
           (typeof bound !== 'string' || !/^https?:\/\//i.test(bound)))) valid = false
      return bound
    }
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, fill(child)]))
  }
  const tree = fill(definition.tree)
  return valid ? parseLiveUISpec({ ...identity, tree }) : null
}
