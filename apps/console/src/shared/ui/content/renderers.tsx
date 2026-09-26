export { CodePreview } from './CodePreview'
import { CodePreview, isCodePreviewPath } from './CodePreview'
import { memo, useMemo, useRef, useState } from 'react'
import { Download, ShieldAlert, Sliders } from 'lucide-react'
import type { PreviewProps } from './contentTypes'
import { Markdown } from '../Markdown'
import { SquareIconButton } from '../SquareIconButton'
import { buildSrcdoc, readThemeVars } from '../widget/widgetSrcdoc'
import { ReactWidgetFrame } from '../widget/ReactWidgetFrame'
import { useWidgetDocument } from '../widget/widgetFrameState'
import { useWidgetWire } from '../widget/useWidgetActionBridge'
import { useArtifactIteration } from '../widget/useArtifactIteration'
import { ArtifactIterationRail } from '../widget/ArtifactIterationRail'
import { sanitizeInlineHtml } from './sanitize'
import { ImagePreview, PdfPreview, CsvPreview, JsonPreview } from '../../../features/files/browse/FilePreviews'
import { PROSE_MEASURE_CLASS } from '../../theme/measure'
import { binaryReference, documentFormat, useExtractedText } from './contentPreviewState'

export const MarkdownPreview = memo(function MarkdownPreview({ content }: PreviewProps) {
  return <div className="px-l py-m"><Markdown>{content}</Markdown></div>
})

export const IframeHtmlPreview = memo(function IframeHtmlPreview({ content, mode, title, iterate }: PreviewProps) {
  const frame = useRef<HTMLIFrameElement>(null)
  const editable = !!iterate
  const source = useMemo(() => buildSrcdoc({ html: content, themeVars: readThemeVars(), mode, editMode: editable }), [content, mode, editable])
  const url = useWidgetDocument(source)
  const iteration = useArtifactIteration(frame, { source: content, target: iterate ?? {} })
  useWidgetWire(frame, { forwardActions: true, ...iteration.wire })
  const [open, setOpen] = useState(false)
  return <div className="flex h-full w-full">
    {url && <iframe ref={frame} src={url} sandbox="allow-scripts" title={title} className="h-full min-w-0 flex-1 border-none bg-surface" />}
    {editable && (open ? <ArtifactIterationRail it={iteration} onClose={() => setOpen(false)} /> : <div className="shrink-0 border-l border-outline-variant/30 p-2">
      <SquareIconButton icon={Sliders} label="Iterate on this artifact — tweak parameters or mark elements" onClick={() => setOpen(true)} iconSize={13} />
    </div>)}
  </div>
})

export const RawHtmlPreview = memo(function RawHtmlPreview({ content }: PreviewProps) {
  const url = useWidgetDocument(content)
  return url ? <iframe src={url} sandbox="allow-scripts" title="HTML preview" className="h-full w-full border-none bg-white" /> : null
})

export const ReactPreview = memo(function ReactPreview({ content, title }: PreviewProps) {
  return <div className="px-l py-m"><ReactWidgetFrame jsx={content} title={title} /></div>
})

function SanitizedEmpty({ what }: { what: string }) {
  return <div className="grid h-full place-items-center p-l"><div className="flex max-w-sm flex-col items-center gap-2 text-center text-on-surface-low">
    <ShieldAlert size={22} className="opacity-40" /><p className="text-sm">Nothing to display.</p>
    <p className="text-xs leading-relaxed">The {what} had no renderable content after sanitizing — script, handlers, and unsafe markup are removed. Switch to Edit to see the raw source.</p>
  </div></div>
}

export const SvgPreview = memo(function SvgPreview({ content }: PreviewProps) {
  const sanitized = useMemo(() => sanitizeInlineHtml(content, 'svg'), [content])
  return content.trim() && !sanitized.trim() ? <SanitizedEmpty what="SVG" /> : <div className="grid h-full place-items-center p-l" dangerouslySetInnerHTML={{ __html: sanitized }} />
})

export const DocumentPreview = memo(function DocumentPreview(props: PreviewProps) {
  const { content } = props
  const format = useMemo(() => documentFormat(content), [content])
  const sanitized = useMemo(() => format === 'html' ? sanitizeInlineHtml(content, 'document') : '', [format, content])
  if (format === 'markdown') return <MarkdownPreview {...props} />
  if (content.trim() && !sanitized.trim()) return <SanitizedEmpty what="document" />
  return <div className={`doc mx-auto ${PROSE_MEASURE_CLASS} px-l py-xl`} dangerouslySetInnerHTML={{ __html: sanitized }} />
})

export const TextPreview = memo(function TextPreview(props: PreviewProps) {
  if (isCodePreviewPath(props.path || props.title)) return <CodePreview {...props} />
  return <pre data-type="body-s" className="overflow-auto whitespace-pre-wrap px-l py-m font-mono leading-relaxed text-on-surface">{props.content}</pre>
})
export const JsonTreePreview = memo(function JsonTreePreview({ content, path }: PreviewProps) {
  return <JsonPreview content={content} name={path} />
})
export const CsvTablePreview = memo(function CsvTablePreview({ content, path }: PreviewProps) {
  return <CsvPreview content={content} name={path || 'data.csv'} />
})
export const ImageFilePreview = memo(function ImageFilePreview({ path, content }: PreviewProps) {
  return <div className="h-full overflow-auto"><ImagePreview path={path} src={binaryReference(content, true)} /></div>
})
export const PdfFilePreview = memo(function PdfFilePreview({ path, content }: PreviewProps) {
  const src = binaryReference(content)
  return src || path ? <PdfPreview path={path} src={src} /> : <PreviewUnavailable label="PDF" />
})
export const VideoFilePreview = memo(function VideoFilePreview({ path, content }: PreviewProps) {
  const src = binaryReference(content, true) || path
  return src ? <div className="grid h-full place-items-center overflow-auto p-m"><video src={src} controls className="max-h-full max-w-full rounded-lg bg-surface-high" /></div> : <PreviewUnavailable label="video" />
})
export const OfficeDocPreview = memo(function OfficeDocPreview({ path, content }: PreviewProps) {
  const raw = binaryReference(content) || path
  return <div className="flex h-full flex-col gap-3 overflow-auto p-m">
    <p className="text-xs leading-relaxed text-on-surface-low">Text preview — download for full formatting. The editable source is whatever this document was generated from.</p>
    {raw ? <a href={raw} download className="inline-flex h-8 w-fit items-center gap-2 rounded-md border border-outline-variant bg-surface-high px-3 text-sm text-on-surface hover:bg-surface-container"><Download size={13} /> Download</a> : <PreviewUnavailable label="document" />}
    <OfficeExtractedText url={raw} />
  </div>
})
function OfficeExtractedText({ url }: { url?: string }) {
  const result = useExtractedText(url)
  if (result.status === 'failed') return <p data-type="body-s" className="text-on-surface-low">Couldn't extract a text preview. The download above is unaffected.</p>
  if (result.status === 'loading') return <p data-type="body-s" className="text-on-surface-low">Reading…</p>
  return result.text.trim() ? <pre data-type="body-s" className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-container p-m text-on-surface-var">{result.text}</pre> : <p data-type="body-s" className="text-on-surface-low">This document has no extractable text.</p>
}
function PreviewUnavailable({ label }: { label: string }) {
  return <p data-type="body-s" className="text-on-surface-low">This {label} is no longer available.</p>
}
