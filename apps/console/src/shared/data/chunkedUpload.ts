
import { errText } from './errText'

const SK = { 'X-Session-Key': 'dashboard:ui' }

export interface UploadProgress {
  loaded: number
  total: number
  pct: number
}

export interface ChunkedUploadOpts {
  target: 'attachment' | 'knowledge' | 'workspace'
  path?: string
  onProgress?: (p: UploadProgress) => void
  signal?: AbortSignal
}

interface Limits { limits: Record<string, number>; single_post_threshold: number }

let _limitsCache: Limits | null = null
async function fetchLimits(): Promise<Limits> {
  if (_limitsCache) return _limitsCache
  const r = await fetch('/api/uploads/limits', { headers: { ...SK } })
  if (!r.ok) throw new Error(`could not fetch upload limits (HTTP ${r.status})`)
  _limitsCache = await r.json()
  return _limitsCache!
}

export async function needsChunked(file: File): Promise<boolean> {
  try {
    const { single_post_threshold } = await fetchLimits()
    return file.size > single_post_threshold
  } catch {
    return false
  }
}

export async function precheck(file: File): Promise<string | null> {
  let limits: Limits['limits']
  try { limits = (await fetchLimits()).limits } catch { return null }
  const cat = categoryOf(file)
  const cap = limits[cat] ?? limits.other
  if (cap && file.size > cap) {
    return `${cat} file too large (max ${humanBytes(cap)}) — this file is ${humanBytes(file.size)}`
  }
  return null
}

export function isAbortError(e: unknown): boolean {
  if (!e) return false
  const err = e as { name?: string; message?: string }
  return err.name === 'AbortError' || /aborted/i.test(err.message || '')
}

export async function chunkedUpload(file: File, opts: ChunkedUploadOpts): Promise<any> {
  const initBody = {
    filename: file.name, size: file.size,
    mime: file.type || guessMime(file.name), target: opts.target,
    ...(opts.path ? { path: opts.path } : {}),
  }
  const initR = await fetch('/api/uploads/init', {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...SK },
    body: JSON.stringify(initBody), signal: opts.signal,
  })
  if (!initR.ok) throw new Error(await errText(initR))
  const { uploadId, partSize, totalParts } = await initR.json()

  let loaded = 0
  for (let i = 0; i < totalParts; i++) {
    if (opts.signal?.aborted) throw new DOMException('upload cancelled', 'AbortError')
    const start = i * partSize
    const blob = file.slice(start, Math.min(start + partSize, file.size))

    await putPartWithRetry(uploadId, i, blob, opts.signal)
    loaded += blob.size
    opts.onProgress?.({ loaded, total: file.size, pct: Math.round((loaded / file.size) * 100) })
  }

  const compR = await fetch(`/api/uploads/${uploadId}/complete`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', ...SK },
    body: '{}', signal: opts.signal,
  })
  if (!compR.ok) throw new Error(await errText(compR))
  return compR.json()
}

const _PART_RETRIES = 4

async function putPartWithRetry(uploadId: string, index: number, blob: Blob, signal?: AbortSignal): Promise<void> {
  let lastErr: Error | null = null
  for (let attempt = 0; attempt < _PART_RETRIES; attempt++) {
    if (signal?.aborted) throw new DOMException('upload cancelled', 'AbortError')
    try {
      const r = await fetch(`/api/uploads/${uploadId}/part?index=${index}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', ...SK },
        body: blob, signal,
      })
      if (r.ok) return
      if (r.status >= 400 && r.status < 500) throw new Error(await errText(r))
      lastErr = new Error(await errText(r))
    } catch (e) {
      if (isAbortError(e)) throw e
      lastErr = e as Error
    }
    if (attempt < _PART_RETRIES - 1) await new Promise((res) => setTimeout(res, 400 * 2 ** attempt))
  }
  throw new Error(`part ${index} failed after ${_PART_RETRIES} attempts: ${lastErr?.message || 'unknown'}`)
}


const _EXT_CAT: Record<string, string> = {
  mp4: 'video', mov: 'video', avi: 'video', mkv: 'video', webm: 'video', m4v: 'video',
  mp3: 'audio', wav: 'audio', ogg: 'audio', flac: 'audio', m4a: 'audio', aac: 'audio',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', bmp: 'image',
  svg: 'image', heic: 'image', heif: 'image', tiff: 'image', tif: 'image',
  zip: 'archive', tar: 'archive', gz: 'archive', tgz: 'archive', bz2: 'archive', xz: 'archive', '7z': 'archive', rar: 'archive',
  pdf: 'document', docx: 'document', doc: 'document', csv: 'document', tsv: 'document',
  xlsx: 'document', xls: 'document', pptx: 'document', ppt: 'document',
  md: 'document', txt: 'document', json: 'document', yaml: 'document', yml: 'document',
}

function categoryOf(file: File): string {
  const top = (file.type || '').split('/', 1)[0].toLowerCase()
  if (top === 'video' || top === 'audio' || top === 'image') return top
  const ext = file.name.split('.').pop()?.toLowerCase() || ''
  return _EXT_CAT[ext] || 'other'
}

function guessMime(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  const map: Record<string, string> = {
    mp4: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm',
    mp3: 'audio/mpeg', wav: 'audio/wav', m4a: 'audio/mp4',
    png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', pdf: 'application/pdf',
  }
  return map[ext] || 'application/octet-stream'
}

export function humanBytes(n: number): string {
  const gb = 1024 ** 3, mb = 1024 ** 2, kb = 1024
  if (n >= gb) { const v = n / gb; return `${v % 1 === 0 ? v : v.toFixed(1)} GB` }
  if (n >= mb) { const v = n / mb; return `${v % 1 === 0 ? v : v.toFixed(1)} MB` }
  if (n >= kb) return `${Math.round(n / kb)} KB`
  return `${n} B`
}

