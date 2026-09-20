export const CRON_EXPR_INVALID_REASON = 'Enter a valid cron expression (minute hour day-of-month month day-of-week).'

const ALIASES = new Set(['@annually', '@yearly', '@monthly', '@weekly', '@daily', '@midnight', '@hourly'])
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
const WEEKDAYS = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

function valueValid(value: string, min: number, max: number, names: string[] = []) {
  const named = names.indexOf(value.toLowerCase())
  if (named >= 0) return true
  return /^\d+$/.test(value) && Number(value) >= min && Number(value) <= max
}

function fieldValid(field: string, index: number) {
  const [min, max, names] = ([
    [0, 59, []], [0, 23, []], [1, 31, []], [1, 12, MONTHS], [0, 7, WEEKDAYS],
  ] as const)[index]

  return field.split(',').every((part) => {
    const step = part.split('/')
    if (step.length > 2 || (step[1] !== undefined && (!/^\d+$/.test(step[1]) || Number(step[1]) < 1))) return false
    const base = step[0]
    if (base === '*' || ((index === 2 || index === 4) && base === '?')) return true
    if (index === 2 && step.length === 1 && (/^L$/i.test(base) || /^(?:\d+W|W\d+)$/i.test(base))) return true

    const nth = base.split('#')
    if (nth.length === 2) return step.length === 1 && index === 4 && valueValid(nth[0], min, max, names) && /^[1-5]$/.test(nth[1])
    if (nth.length > 2) return false
    if (index === 4 && step.length === 1 && /^L(?:[0-7]|sun|mon|tue|wed|thu|fri|sat)$/i.test(base)) return true

    const range = base.split('-')
    return range.length <= 2 && range.every((value) => valueValid(value, min, max, names))
  })
}

export function cronExprInvalidReason(expression: string): string | undefined {
  const value = expression.trim()
  if (ALIASES.has(value.toLowerCase())) return undefined
  const fields = value.split(/\s+/)
  return fields.length === 5 && fields.every(fieldValid) ? undefined : CRON_EXPR_INVALID_REASON
}
