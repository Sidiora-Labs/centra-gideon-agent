import { Children, isValidElement, memo, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { physics } from '../theme/motion'
import { fvs } from '../theme/fontWeight'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeRaw from 'rehype-raw'
import rehypeKatex from 'rehype-katex'
import hljs from 'highlight.js/lib/common'
import { Play, Copy, Check, ImageOff, RefreshCw } from 'lucide-react'
import type { PluggableList } from 'unified'
import { requestRunInTerminal } from '../../features/terminal/terminalBridge'
import { api } from '../data/api'
import { createElement } from 'react'
import { parseWidgetBlocks } from './widget/blocks'
import { embedFor } from './content/contentTypes'
import { MermaidBlock } from './widget/MermaidBlock'
import type { MemoryCitation } from '../../features/chat/chatTypes'
import 'katex/dist/katex.min.css'
import { copyText } from '../../app/shell/clipboard'


const SHELL_LANGS = new Set(['shell', 'bash', 'sh', 'zsh', 'console', 'shellsession', 'fish'])

function DiffBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => { if (await copyText(code, 'the code')) { setCopied(true); setTimeout(() => setCopied(false), 1500) } }
  return (
    <div className="group/code my-3 overflow-hidden rounded-lg bg-surface-low">
      <div className="flex items-center gap-2 px-m pt-2">
        <span data-type="caption" className="uppercase tracking-wide text-on-surface-low">diff</span>
        <button type="button" onClick={copy} aria-label="Copy diff" title={copied ? 'Copied' : 'Copy'}
          className="ml-auto inline-flex size-6 items-center justify-center rounded text-on-surface-low opacity-0 transition-opacity hover:bg-surface-high hover:text-on-surface group-hover/code:opacity-100 focus-within:opacity-100"
          style={copied ? { color: 'var(--color-success)' } : undefined}>
          <AnimatePresence mode="wait" initial={false}>
            <motion.span key={copied ? 'ok' : 'copy'} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={physics.playful} className="grid place-items-center">
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </motion.span>
          </AnimatePresence>
        </button>
      </div>
      {
}
      {
}
      {
}
      <pre tabIndex={0} role="group" aria-label="Diff" data-type="body-s"
        className="overflow-x-auto px-m py-2 leading-relaxed font-mono focus-visible:-outline-offset-2">
        {code.split('\n').map((ln, i) => {
          const add = /^\+(?!\+)/.test(ln), del = /^-(?!-)/.test(ln), hunk = /^@@/.test(ln)
          return (
            <div key={i} style={add ? { background: 'color-mix(in srgb, var(--color-ok) 14%, transparent)', color: 'var(--color-ok)' }
              : del ? { background: 'color-mix(in srgb, var(--color-danger) 14%, transparent)', color: 'var(--color-danger)' }
              : hunk ? { color: 'var(--color-primary)' } : { color: 'var(--color-on-surface-var)' }}>{ln || ' '}</div>
          )
        })}
      </pre>
    </div>
  )
}

function isDiff(code: string, lang?: string): boolean {
  if (lang === 'diff') return true
  const lines = code.split('\n')
  return lines.filter((l) => /^@@|^[+-][^+-]/.test(l)).length >= 2
}

function artifactSlugFromSrc(src: string): string {
  const m = src.match(/\/api\/artifacts\/([^/?]+)\/raw\b/)
  return m ? decodeURIComponent(m[1]) : ''
}

function InlineArtifactImage({ src, alt, chatSessionKey }: {
  src: string; alt: string; chatSessionKey?: string
}) {
  const [failed, setFailed] = useState(false)
  const [bust, setBust] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const slug = artifactSlugFromSrc(src)

  const regenerate = async () => {
    if (!slug) return
    setBusy(true); setErr('')
    try {
      await api.regenerateArtifactImage(slug, { session: chatSessionKey, prompt: alt })
      setBust(`${src.includes('?') ? '&' : '?'}_r=${Date.now()}`)
      setFailed(false)
    } catch (e) {
      setErr((e as Error)?.message || 'Regenerate failed')
    } finally { setBusy(false) }
  }

  if (failed) {
    return (
      <div className="my-2 flex max-w-md flex-col gap-2 rounded-lg border border-outline-variant/40 bg-surface-low px-4 py-3">
        <div data-type="body-s" className="flex items-center gap-2 text-on-surface-low">
          <ImageOff size={14} className="shrink-0" />
          <span>This image is no longer available.</span>
        </div>
        {alt && (
          <div data-type="body-s" className="text-on-surface-var">
            <span className="text-on-surface-low">Prompt:</span> {alt}
          </div>
        )}
        {chatSessionKey && slug && (
          <button type="button" onClick={regenerate} disabled={busy}
            data-type="caption"
            className="mt-0.5 inline-flex w-fit items-center gap-1.5 rounded-md bg-surface-high px-2.5 py-1 text-on-surface transition-colors hover:bg-surface-highest disabled:opacity-60">
            <RefreshCw size={12} className={busy ? 'animate-spin' : ''} /> {busy ? 'Regenerating…' : 'Regenerate image'}
          </button>
        )}
        {err && <span data-type="caption" style={{ color: 'var(--color-danger)' }}>{err}</span>}
      </div>
    )
  }
  return (
    <img src={src + bust} alt={alt} loading="lazy" onError={() => setFailed(true)}
      className="my-2 max-h-[28rem] max-w-full rounded-lg border border-outline-variant/40 object-contain" />
  )
}

function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  let html = ''
  try {
    html = lang && hljs.getLanguage(lang)
      ? hljs.highlight(code, { language: lang }).value
      : hljs.highlightAuto(code).value
  } catch { html = code.replace(/[&<>]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[ch]!)) }
  const [copied, setCopied] = useState(false)
  const runnable = !!lang && SHELL_LANGS.has(lang.toLowerCase())
  const copy = async () => { if (await copyText(code, 'the code')) { setCopied(true); setTimeout(() => setCopied(false), 1500) } }
  const run = () => requestRunInTerminal(code.trim())
  return (
    <div className="group/code my-3 overflow-hidden rounded-lg bg-surface-low">
      <div className="flex items-center gap-2 px-m pt-2">
        {lang && <span data-type="caption" className="uppercase tracking-wide text-on-surface-low">{lang}</span>}
        <div className="ml-auto flex items-center gap-0.5 opacity-0 transition-opacity group-hover/code:opacity-100 focus-within:opacity-100">
          {runnable && (
            <button type="button" onClick={run} title="Run in terminal" data-type="caption"
              className="inline-flex h-6 items-center gap-1 rounded px-1.5 text-on-surface-low hover:bg-surface-high hover:text-primary">
              <Play size={11} /> Run
            </button>
          )}
          <button type="button" onClick={copy} title={copied ? 'Copied' : 'Copy'} aria-label="Copy code"
            className="inline-flex size-6 items-center justify-center rounded text-on-surface-low hover:bg-surface-high hover:text-on-surface"
            style={copied ? { color: 'var(--color-success)' } : undefined}>
            { }
            <AnimatePresence mode="wait" initial={false}>
              <motion.span key={copied ? 'ok' : 'copy'} initial={{ scale: 0, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0, opacity: 0 }} transition={physics.playful} className="grid place-items-center">
                {copied ? <Check size={12} /> : <Copy size={12} />}
              </motion.span>
            </AnimatePresence>
          </button>
        </div>
      </div>
      {
}
      {
}
      <pre tabIndex={0} role="group" aria-label={lang ? `${lang} code` : 'Code'} data-type="body-s"
        className="overflow-x-auto px-m py-2 leading-relaxed focus-visible:-outline-offset-2"><code className="hljs font-mono" dangerouslySetInnerHTML={{ __html: html }} /></pre>
    </div>
  )
}

function safeHref(href: unknown): string | undefined {
  if (typeof href !== 'string') return undefined
  const h = href.trim()
  if (!h) return undefined
  if (/^(\/|\.|#|mailto:|tel:)/i.test(h)) return h
  if (/^https?:\/\//i.test(h)) return h
  if (/^[a-z][a-z0-9+.-]*:/i.test(h)) return undefined
  return h
}

const FILE_PATH_RE = /^(?:~|\.{0,2}\/)?[\w.\-]+(?:\/[\w.\-]+)*\.\w{1,8}\/?$/
function looksLikeFile(s: string): boolean {
  const t = s.trim()
  return t.length <= 200 && !t.includes(' ') && FILE_PATH_RE.test(t)
}

function renderCode({ className, children, block = false }: any) {
  const m = /language-(\w+)/.exec(className || '')
  const str = String(children).replace(/\n$/, '')
  if (!block) return <code className="rounded-sm bg-surface-high px-1.5 py-0.5 text-[0.85em] font-mono text-primary-emphasis">{children}</code>
  const lang = m?.[1]
  if (lang === 'mermaid') return <MermaidBlock code={str} />
  if (isDiff(str, lang)) return <DiffBlock code={str} />
  return <CodeBlock code={str} lang={lang} />
}

const COMPONENTS: Record<string, React.ComponentType<any>> = {
  code: renderCode,
  pre({ children }: any) {
    const parts = Children.toArray(children)
    const code = parts[0]
    if (parts.length === 1 && isValidElement<{ className?: string; children?: React.ReactNode }>(code)) {
      return renderCode({ ...code.props, block: true })
    }
    return <pre>{children}</pre>
  },
  table({ children }: any) { return <div className="my-3 overflow-x-auto"><table data-type="body-s" className="w-full border-collapse">{children}</table></div> },
  th({ children }: any) { return <th className="border-b border-outline-variant/50 bg-surface-high px-m py-2 text-left text-on-surface-var" style={fvs(500)}>{children}</th> },
  td({ children }: any) { return <td className="border-b border-outline-variant/30 px-m py-2">{children}</td> },
  a({ href, children }: any) {
    const safe = safeHref(href)
    if (!safe) return <span className="text-primary underline decoration-primary/40 underline-offset-2" title="Link removed (unsafe URL)">{children}</span>
    return <a href={safe} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2 decoration-primary/40 hover:decoration-primary">{children}</a>
  },
  img({ src, alt }: any) {
    const safe = typeof src === 'string' && (/^(\/|https?:\/\/)/.test(src.trim()) || /^data:image\//i.test(src.trim()))
      ? src.trim() : undefined
    if (!safe) return <span className="text-on-surface-low italic">{alt || 'image'}</span>
    return <InlineArtifactImage src={safe} alt={alt || ''} />
  },
  blockquote({ children }: any) { return <blockquote className="my-2 border-l border-outline-variant pl-m italic text-on-surface-var">{children}</blockquote> },
  hr() { return <hr className="my-4 border-outline-variant/40" /> },
  h1({ children }: any) { return <h1 className="mt-4 mb-2 text-on-surface" data-type="headline-s">{children}</h1> },
  h2({ children }: any) { return <h2 className="mt-3 mb-2 text-on-surface text-[1.0625rem]" style={fvs(500)}>{children}</h2> },
  h3({ children }: any) { return <h3 className="mt-3 mb-1.5 text-on-surface text-[1.0625rem]" style={fvs(500)}>{children}</h3> },
  h4({ children }: any) { return <h4 data-type="title-m" className="mt-2 mb-1 text-on-surface">{children}</h4> },
  ul({ children }: any) { return <ul className="my-2 list-disc space-y-1 pl-7 marker:text-on-surface-low">{children}</ul> },
  ol({ children }: any) { return <ol className="my-2 list-decimal space-y-1 pl-7 marker:text-on-surface-low">{children}</ol> },
  li({ children }: any) { return <li data-type="body-m" className="leading-relaxed">{children}</li> },
  p({ children }: any) { return <p data-type="body-m" className="my-1.5 leading-relaxed">{children}</p> },
  strong({ children }: any) { return <strong className="text-on-surface" style={fvs(600)}>{children}</strong> },
  em({ children }: any) { return <em className="italic">{children}</em> },
}

const REMARK: PluggableList = [remarkGfm, [remarkMath, { singleDollarTextMath: false }]]
const REHYPE: PluggableList = [[rehypeRaw, { passThrough: ['math', 'inlineMath'] }], rehypeKatex]

const BARE_FILE_RE = /((?:~|\.{0,2}\/)?[\w.\-]+(?:\/[\w.\-]+)+\.\w{1,8})/g

function linkifyFiles(children: any, onFileClick: (path: string) => void): any {
  return (Array.isArray(children) ? children : [children]).flatMap((child, ci) => {
    if (typeof child !== 'string') return [child]
    const parts: any[] = []
    let last = 0, m: RegExpExecArray | null
    BARE_FILE_RE.lastIndex = 0
    while ((m = BARE_FILE_RE.exec(child)) !== null) {
      const path = m[1]
      if (m.index > last) parts.push(child.slice(last, m.index))
      parts.push(
        <button key={`${ci}-${m.index}`} type="button" onClick={() => onFileClick(path)} title={`Open ${path}`}
          className="align-baseline font-mono text-[0.95em] text-primary underline decoration-primary/40 underline-offset-2 transition-colors hover:decoration-primary">
          {path}
        </button>,
      )
      last = m.index + path.length
    }
    if (last < child.length) parts.push(child.slice(last))
    return parts.length ? parts : [child]
  })
}

const MEMORY_CITE_RE = /\[Memory (\d{1,4})\]/g

function linkifyMemory(children: any, citations: MemoryCitation[]): any {
  const byN = new Map(citations.map((c) => [c.n, c]))
  return (Array.isArray(children) ? children : [children]).flatMap((child, ci) => {
    if (typeof child !== 'string') return [child]
    const parts: any[] = []
    let last = 0, m: RegExpExecArray | null
    MEMORY_CITE_RE.lastIndex = 0
    while ((m = MEMORY_CITE_RE.exec(child)) !== null) {
      const n = Number(m[1])
      const cite = byN.get(n)
      if (m.index > last) parts.push(child.slice(last, m.index))
      if (!cite || !cite.id) {
        parts.push(m[0])
      } else {
        const href = `#/settings/memory?tab=studio&sel=${encodeURIComponent(`epi:${cite.id}`)}`
        parts.push(
          <a key={`${ci}-${m.index}`} href={href} title={cite.preview || `Memory ${n}`}
            className="mx-0.5 inline-flex items-baseline rounded-sm bg-surface-high px-1.5 align-baseline text-[0.8em] text-primary-emphasis no-underline decoration-primary/40 transition-colors hover:bg-surface-highest hover:underline">
            Memory {n}
          </a>,
        )
      }
      last = m.index + m[0].length
    }
    if (last < child.length) parts.push(child.slice(last))
    return parts.length ? parts : [child]
  })
}

function componentsWith(
  onFileClick?: (path: string) => void,
  chatSessionKey?: string,
  citations?: MemoryCitation[],
): Record<string, React.ComponentType<any>> {
  if (!onFileClick && !chatSessionKey && !(citations && citations.length)) return COMPONENTS
  const base: Record<string, React.ComponentType<any>> = { ...COMPONENTS }
  if (chatSessionKey) {
    base.img = ({ src, alt }: any) => {
      const safe = typeof src === 'string' && (/^(\/|https?:\/\/)/.test(src.trim()) || /^data:image\//i.test(src.trim()))
        ? src.trim() : undefined
      if (!safe) return <span className="text-on-surface-low italic">{alt || 'image'}</span>
      return <InlineArtifactImage src={safe} alt={alt || ''} chatSessionKey={chatSessionKey} />
    }
  }
  const cites = citations && citations.length ? citations : null
  const L = (children: any) => {
    let out = onFileClick ? linkifyFiles(children, onFileClick) : children
    if (cites) out = linkifyMemory(out, cites)
    return out
  }
  return {
    ...base,
    code({ className, children }: any) {
      const str = String(children).replace(/\n$/, '')
      if (onFileClick && !className && looksLikeFile(str)) {
        return (
          <button type="button" onClick={() => onFileClick(str.trim())} title={`Open ${str.trim()}`}
            className="rounded-sm bg-surface-high px-1.5 py-0.5 align-baseline text-[0.85em] font-mono text-primary-emphasis underline decoration-primary/40 underline-offset-2 transition-colors hover:bg-surface-highest hover:decoration-primary">
            {children}
          </button>
        )
      }
      return renderCode({ className, children })
    },
    p({ children }: any) { return <p data-type="body-m" className="my-1.5 leading-relaxed">{L(children)}</p> },
    li({ children }: any) { return <li data-type="body-m" className="leading-relaxed">{L(children)}</li> },
    td({ children }: any) { return <td className="border-b border-outline-variant/30 px-m py-2">{L(children)}</td> },
  }
}

function stringifyChildren(v: unknown): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  if (Array.isArray(v)) return v.map(stringifyChildren).filter(Boolean).join('\n')
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `- **${k}:** ${stringifyChildren(val)}`).join('\n')
  }
  return String(v)
}

function MarkdownText({ children, onFileClick, chatSessionKey, citations, inline = false }: {
  children: string; inline?: boolean; onFileClick?: (path: string) => void; chatSessionKey?: string; citations?: MemoryCitation[]
}) {
  const components = componentsWith(onFileClick, chatSessionKey, citations)
  return <ReactMarkdown remarkPlugins={REMARK} rehypePlugins={inline ? [] : REHYPE}
    skipHtml={inline} disallowedElements={inline ? undefined : ['script', 'style', 'iframe', 'object', 'embed', 'form', 'input', 'button']}
    allowedElements={inline ? ['p', 'strong', 'em', 'del', 'a', 'code', 'br'] : undefined} unwrapDisallowed={inline}
    components={inline ? { ...components, p: ({ children }) => <span>{children}{' '}</span>, code: ({ children }) => <code className="font-mono">{children}</code> } : components}>{children}</ReactMarkdown>
}

export const Markdown = memo(function Markdown({ children, className, onFileClick, chatSessionKey, messageTs, streaming, citations, inline = false }: {
  children: unknown; inline?: boolean; className?: string; onFileClick?: (path: string) => void
  chatSessionKey?: string
  messageTs?: string
  streaming?: boolean
  citations?: MemoryCitation[]
}) {
  const text = typeof children === 'string' ? children : stringifyChildren(children)
  if (!text.trim()) return null
  if (inline) return <span className={className}><MarkdownText inline onFileClick={onFileClick} chatSessionKey={chatSessionKey} citations={citations}>{text}</MarkdownText></span>
  const segments = parseWidgetBlocks(text, streaming)
  if (segments.length === 1 && segments[0].type === 'md') {
    return <div className={`text-on-surface ${className ?? ''}`}><MarkdownText onFileClick={onFileClick} chatSessionKey={chatSessionKey} citations={citations}>{text}</MarkdownText></div>
  }
  let wi = 0
  return (
    <div className={`flow-root text-on-surface ${className ?? ''}`}>
      {segments.map((seg, i) => {
        if (seg.type !== 'widget') return <MarkdownText key={i} onFileClick={onFileClick} chatSessionKey={chatSessionKey} citations={citations}>{seg.content}</MarkdownText>
        const embed = embedFor(seg.kind)
        if (!embed) return null
        if (!embed.streaming && !seg.complete) return null
        const widgetIndex = wi++
        return createElement(embed.render, { key: i, content: seg.html, title: seg.title, slug: seg.slug, messageTs, widgetIndex, streaming: !seg.complete })
      })}
    </div>
  )
})
