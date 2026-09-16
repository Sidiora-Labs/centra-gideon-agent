import { sanitizeInlineHtml } from './sanitize'
import { loadInfographicEngine } from './antvEngine'
import { PROSE_MEASURE } from '../../theme/measure'
import { copyText } from '../../../app/shell/clipboard'

function filename(title: string, fallback: string, extension: string) {
  return `${title.replace(/[^a-zA-Z0-9-_ ]/g, '').trim() || fallback}.${extension}`
}
function download(name: string, source: string, mime: string): void {
  const url = URL.createObjectURL(new Blob([source], { type: mime }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = name
  try { document.body.append(anchor); anchor.click() }
  finally { anchor.remove(); setTimeout(() => URL.revokeObjectURL(url), 60_000) }
}

export function documentExportSource(content: string, title: string): string {
  const page = document.implementation.createHTMLDocument(title)
  page.documentElement.lang = 'en'
  const encoding = page.createElement('meta')
  encoding.setAttribute('charset', 'utf-8')
  const viewport = page.createElement('meta')
  viewport.name = 'viewport'
  viewport.content = 'width=device-width, initial-scale=1'
  const style = page.createElement('style')
  style.textContent = `
  :root { color-scheme: light dark; }
  body { margin: 0; font: 16px/1.7 -apple-system, system-ui, "Segoe UI", Roboto, sans-serif; color: #1a1a1a; background: #fff; }
  main { max-width: ${PROSE_MEASURE}; margin: 0 auto; padding: 3rem 1.5rem; }
  h1,h2,h3,h4 { line-height: 1.25; margin: 2em 0 0.6em; font-weight: 650; }
  h1 { font-size: 2rem; } h2 { font-size: 1.5rem; } h3 { font-size: 1.2rem; }
  p, li { margin: 0.6em 0; } ul,ol { padding-left: 1.4em; }
  a { color: #2f54eb; } img { max-width: 100%; height: auto; }
  blockquote { margin: 1em 0; padding-left: 1em; border-left: 3px solid #ddd; color: #555; }
  pre { overflow:auto; background:#f5f5f5; padding:1em; border-radius:8px; } code { font-family: ui-monospace, monospace; }
  table { border-collapse: collapse; width: 100%; } th,td { border: 1px solid #ddd; padding: 0.5em 0.7em; text-align: left; }
  @media (prefers-color-scheme: dark) { body { color:#e6e6e6; background:#141414; } blockquote{color:#aaa;border-color:#333} pre{background:#1e1e1e} th,td{border-color:#333} }
`
  page.head.prepend(encoding, viewport)
  page.head.append(style)
  const main = page.createElement('main')
  main.innerHTML = sanitizeInlineHtml(content, 'document')
  page.body.append(main)
  return '<!DOCTYPE html>\n' + page.documentElement.outerHTML
}

export function exportDocumentHtml(content: string, title: string): void {
  download(filename(title, 'document', 'html'), documentExportSource(content, title), 'text/html;charset=utf-8')
}

export async function copyDocumentHtml(content: string): Promise<void> {
  await copyText(sanitizeInlineHtml(content, 'document'), 'the document HTML')
}

export async function exportInfographicSvg(content: string, title: string): Promise<void> {
  const host = document.createElement('div')
  host.style.cssText = 'position:fixed;left:-99999px;top:0;width:960px;height:720px'
  document.body.append(host)
  let dispose: (() => void) | undefined
  try {
    const Renderer = await loadInfographicEngine()
    const renderer = new Renderer({ container: host, width: 960, height: 720 })
    dispose = () => renderer.destroy()
    renderer.render(content)
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
    const svg = host.querySelector('svg')
    if (svg) download(filename(title, 'infographic', 'svg'), new XMLSerializer().serializeToString(svg), 'image/svg+xml;charset=utf-8')
  } finally {
    try { dispose?.() } finally { host.remove() }
  }
}
