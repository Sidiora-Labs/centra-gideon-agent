
const MAX_INLINE = 200

export interface ErrEnvelope {
  message: string
  code: string
  detail?: unknown
}

export async function errEnvelope(r: Response): Promise<ErrEnvelope> {
  const text = (await r.text().catch(() => '')).trim()
  const isServerErr = r.status >= 500
  let wasJson = false
  let code = ''
  let message = ''
  let detail: unknown
  try {
    const parsed = JSON.parse(text)
    wasJson = true
    if (parsed && typeof parsed === 'object') {
      for (const key of ['error', 'detail'] as const) {
        const v = (parsed as Record<string, unknown>)[key]
        if (typeof v === 'string' && v.trim()) { message = v.trim(); break }
        if (v && typeof v === 'object' && !Array.isArray(v)) {
          const c = (v as Record<string, unknown>).code
          if (!code && typeof c === 'string' && c.trim()) code = c.trim()
          const det = (v as Record<string, unknown>).detail
          if (detail === undefined && det !== undefined) detail = det
          const msg = (v as Record<string, unknown>).message
          if (typeof msg === 'string' && msg.trim()) { message = msg.trim(); break }
        }
      }
    }
  } catch {   }
  if (!message) {
    message = wasJson || !text || text.startsWith('<') || text.length > MAX_INLINE || isServerErr
      ? `HTTP ${r.status}`
      : text
  }
  return { message, code, detail }
}

export async function errText(r: Response): Promise<string> {
  return (await errEnvelope(r)).message
}

const OPAQUE_FAILURE = [
  /^failed to fetch$/i,
  /^load failed$/i,
  /^networkerror\b/i,
  /^network ?error$/i,
  /^typeerror: failed to fetch$/i,
  /^the internet connection appears to be offline\.?$/i,
  /^HTTP \d{3}$/,
]

export function readableErrText(error: unknown): string {
  const raw = (error instanceof Error ? error.message : typeof error === 'string' ? error : '').trim()
  if (!raw) return ''
  return OPAQUE_FAILURE.some((re) => re.test(raw)) ? '' : raw
}
