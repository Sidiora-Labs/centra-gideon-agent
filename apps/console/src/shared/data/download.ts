export function downloadText(filename: string, content: string, mime = 'text/plain;charset=utf-8'): void {
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function safeFilename(name: string, fallback = 'download'): string {
  const base = (name || '').trim().replace(/[\s/\\:*?"<>|]+/g, '-').replace(/^-+|-+$/g, '')
  return base || fallback
}
