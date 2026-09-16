const UI_PREFIX = '[UI] '
const TRUNCATION_MARKER = '…truncated'
export const MAX_ACTION_TEXT_BYTES = 16 * 1024
export const WIDGET_ACTION_EVENT = 'ne:widget-action'

export interface WidgetActionMeta { slug?: string; label?: string }

function boundedTurn(value: string): string {
  const encoder = new TextEncoder()
  if (encoder.encode(value).byteLength <= MAX_ACTION_TEXT_BYTES) return value
  const budget = MAX_ACTION_TEXT_BYTES - encoder.encode(TRUNCATION_MARKER).byteLength
  let bytes = 0
  let end = 0
  for (const character of value) {
    const size = encoder.encode(character).byteLength
    if (bytes + size > budget) break
    bytes += size
    end += character.length
  }
  const prefix = value.slice(0, end).replace(/[\uD800-\uDBFF]$/, '')
  return prefix + TRUNCATION_MARKER
}

export function finishActionText(body: string, live?: { saved: boolean; slug: string }): string {
  const text = boundedTurn(UI_PREFIX + body)
  return live?.saved && live.slug ? `${text} (refresh artifact "${live.slug}" in place)` : text
}

export function composeWidgetActionText(action: string, payload: unknown, live?: { saved: boolean; slug: string }): string | null {
  try {
    const fields = payload ? Object.keys(payload) : []
    return finishActionText(fields.length ? `${action}: ${JSON.stringify(payload)}` : action, live)
  } catch {
    return null
  }
}

export function publishWidgetAction(text: string, meta: WidgetActionMeta = {}): void {
  const event = new CustomEvent(WIDGET_ACTION_EVENT, { detail: { text, ...meta } })
  window.dispatchEvent(event)
}
