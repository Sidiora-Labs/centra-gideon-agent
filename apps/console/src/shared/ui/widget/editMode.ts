import { sanitizeCssValue } from './cssSanitize'

export const EDITMODE_BEGIN = '/*EDITMODE-BEGIN*/'
export const EDITMODE_END = '/*EDITMODE-END*/'
export const MAX_EDIT_PARAMS = 8
export const EDIT_KEY_RE = /^[a-zA-Z][a-zA-Z0-9-]*$/
export const EDIT_MODE_SET_KEYS = '__edit_mode_set_keys'
export const EDIT_MODE_READ_KEYS = '__edit_mode_read_keys'
export const EDIT_MODE_ANNOTATE = '__edit_mode_annotate'
export type EditParamType = 'color' | 'range' | 'select' | 'toggle'
export interface EditModeParam {
  key: string; label: string; type: EditParamType; value: string
  min?: number; max?: number; step?: number; unit?: string
  options?: string[]; on?: string; off?: string
}
export interface EditModeBlock { params: EditModeParam[]; dropped: number }
export interface EditKey { key: string; value: string }

type RecordValue = Record<string, unknown>
const recordOf = (value: unknown): RecordValue | null => value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : null
const finite = (value: unknown, fallback?: number): number | undefined => typeof value === 'number' && Number.isFinite(value) ? value : fallback

function declaration(source: string) {
  const opening = source.indexOf(EDITMODE_BEGIN)
  if (opening < 0) return null
  const start = opening + EDITMODE_BEGIN.length
  const end = source.indexOf(EDITMODE_END, start)
  if (end < 0) return null
  try {
    const fields = recordOf(JSON.parse(source.slice(start, end)))
    return fields ? { start, end, fields } : null
  } catch { return null }
}

type DescriptorReader = (base: EditModeParam, declaration: RecordValue) => EditModeParam | null
const readers: Record<EditParamType, DescriptorReader> = {
  color: base => base,
  range: (base, raw) => {
    const min = finite(raw.min, 0)!
    const max = finite(raw.max)
    if (max === undefined || max <= min) return null
    return { ...base, min, max, step: finite(raw.step, 1), unit: typeof raw.unit === 'string' && /^[a-z%]{0,4}$/.test(raw.unit) ? raw.unit : '' }
  },
  select: (base, raw) => {
    const available = Array.isArray(raw.options) ? raw.options.map(sanitizeCssValue).filter(Boolean) : []
    if (available.length < 2) return null
    const options = available.slice(0, 12)
    return { ...base, options, value: options.includes(base.value) ? base.value : options[0] }
  },
  toggle: (base, raw) => {
    const on = sanitizeCssValue(raw.on)
    const off = sanitizeCssValue(raw.off)
    return on && off ? { ...base, on, off, value: base.value === on || base.value === off ? base.value : off } : null
  },
}

function parameter(key: string, input: unknown): EditModeParam | null {
  const raw = recordOf(input)
  if (!EDIT_KEY_RE.test(key) || !raw || typeof raw.type !== 'string' || !Object.hasOwn(readers, raw.type)) return null
  const type = raw.type as EditParamType
  const value = sanitizeCssValue(raw.value)
  if (!value) return null
  const label = typeof raw.label === 'string' && raw.label.trim() ? raw.label.trim().slice(0, 60) : key
  return readers[type]({ key, label, type, value }, raw)
}

export function parseEditModeBlock(source: string): EditModeBlock | null {
  const parsed = declaration(source)
  if (!parsed) return null
  const entries = Object.entries(parsed.fields)
  if (!entries.length) return null
  const valid = entries.flatMap(([key, value]) => {
    const descriptor = parameter(key, value)
    return descriptor ? [descriptor] : []
  }).slice(0, MAX_EDIT_PARAMS)
  return { params: valid, dropped: entries.length - valid.length }
}

export function rewriteEditModeBlock(source: string, values: Record<string, string>): string {
  const parsed = declaration(source)
  if (!parsed) return source
  let changed = false
  const next = Object.fromEntries(Object.entries(parsed.fields).map(([key, descriptor]) => {
    const field = recordOf(descriptor)
    const value = Object.hasOwn(values, key) && EDIT_KEY_RE.test(key) ? sanitizeCssValue(values[key]) : ''
    if (!field || !value || field.value === value) return [key, descriptor]
    changed = true
    return [key, { ...field, value }]
  }))
  return changed ? `${source.slice(0, parsed.start)}\n${JSON.stringify(next, null, 2)}\n${source.slice(parsed.end)}` : source
}

export function hexForPicker(value: string): string {
  const digits = value.trim().toLowerCase()
  if (!/^#(?:[0-9a-f]{3}|[0-9a-f]{6})$/.test(digits)) return ''
  return digits.length === 7 ? digits : '#' + [...digits.slice(1)].map(digit => digit.repeat(2)).join('')
}

export function rangeNumber(param: EditModeParam): number {
  const min = param.min ?? 0
  const max = param.max ?? 100
  const candidate = Number.parseFloat(param.value)
  return Number.isFinite(candidate) ? Math.max(min, Math.min(max, candidate)) : min
}
