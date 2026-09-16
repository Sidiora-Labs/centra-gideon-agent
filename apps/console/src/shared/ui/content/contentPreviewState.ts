import { useEffect, useState } from 'react'
import { api } from '../../data/api'

const htmlStructure = /<(?:h[1-6]|p|div|section|article|main|header|footer|nav|aside|ul|ol|li|table|thead|tbody|tr|td|th|blockquote|pre|figure)\b[^>]*>/i
const markdownStructure = [/^#{1,6}\s+\S/m, /\*\*[^*\n]+\*\*/, /^```/m, /^\s*\|.+\|\s*$/m, /^\s*[-*+]\s+\S/m, /^\s*>\s+\S/m, /\[[^\]]+\]\([^)]+\)/]
export function documentFormat(content: string): 'markdown' | 'html' {
  return content.trim() && !htmlStructure.test(content) && markdownStructure.some(marker => marker.test(content)) ? 'markdown' : 'html'
}
export function binaryReference(content: string, allowData = false): string | undefined {
  return (allowData ? /^(https?:|data:|\/)/ : /^(https?:|\/)/).test(content) ? content : undefined
}
export function artifactReference(url?: string): string | null {
  const encoded = url?.match(/\/api\/artifacts\/([^/?]+)\/raw/)?.[1]
  if (!encoded) return null
  try { return decodeURIComponent(encoded) } catch { return null }
}
export function useExtractedText(url?: string) {
  const [result, setResult] = useState<{ status: 'loading' | 'failed' | 'ready'; text: string }>({ status: 'loading', text: '' })
  useEffect(() => {
    const slug = artifactReference(url)
    let current = true
    if (!url) { setResult({ status: 'loading', text: '' }); return }
    if (!slug) { setResult({ status: 'failed', text: '' }); return }
    setResult({ status: 'loading', text: '' })
    api.artifactExtractedText(slug).then(value => {
      if (current) setResult({ status: 'ready', text: String(value.text ?? '') })
    }).catch(() => { if (current) setResult({ status: 'failed', text: '' }) })
    return () => { current = false }
  }, [url])
  return result
}
