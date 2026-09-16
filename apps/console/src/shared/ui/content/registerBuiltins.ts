import { lazy } from 'react'
import {
  Box, Globe, Hash, Image, Braces, Code2, FileText, Table, FileCode, BarChart3, ScrollText, Film, Presentation, LayoutDashboard, type LucideIcon,
} from 'lucide-react'
import { registerContentType, type ContentType } from './contentTypes'
import { HtmlWidgetEmbed, ReactWidgetEmbed } from './chatEmbeds'
import { GenUiWidget } from '../genui/GenUiWidget'
import { exportDocumentHtml, copyDocumentHtml, exportInfographicSvg } from './exporters'


type RendererModule = typeof import('./renderers')
function renderer(name: keyof RendererModule) {
  return lazy(async () => {
    const module = await import('./renderers')
    return { default: module[name] }
  })
}
const Markdown = renderer('MarkdownPreview')
const DocumentR = renderer('DocumentPreview')
const IframeHtml = renderer('IframeHtmlPreview')
const RawHtml = renderer('RawHtmlPreview')
const ReactR = renderer('ReactPreview')
const Svg = renderer('SvgPreview')
const Text = renderer('TextPreview')
const JsonTree = renderer('JsonTreePreview')
const CsvTable = renderer('CsvTablePreview')
const ImageFile = renderer('ImageFilePreview')
const PdfFile = renderer('PdfFilePreview')
const VideoFile = renderer('VideoFilePreview')
const OfficeDocPreview = renderer('OfficeDocPreview')
const Infographic = lazy(async () => ({ default: (await import('./InfographicView')).InfographicView }))
const PRIMARY = 'var(--color-primary)'

function builtinDefinitions(): ContentType[] { return [
  {
    id: 'widget', label: 'Widget', icon: Box, tone: PRIMARY,
    kinds: ['widget'],
    preview: { render: IframeHtml, sandboxed: true, streaming: true },
    embed: { render: HtmlWidgetEmbed, streaming: true },
    security: { sandbox: true },
    commentable: false,
  },
  {
    id: 'genui', label: 'Generated UI', icon: LayoutDashboard, tone: PRIMARY,
    embed: { render: GenUiWidget, streaming: true },
    commentable: false,
  },
  {
    id: 'html', label: 'HTML', icon: Globe, tone: '#e06c4f',
    kinds: ['html'], exts: ['html', 'htm'], mimes: ['text/html'],
    preview: { render: RawHtml, sandboxed: true },
    edit: { language: 'html' },
    security: { sandbox: true },
    commentable: false,
  },
  {
    id: 'react', label: 'React', icon: Box, tone: '#61dafb',
    kinds: ['react'], exts: ['jsx', 'tsx'],
    preview: { render: ReactR, sandboxed: true },
    embed: { render: ReactWidgetEmbed, streaming: false },
    edit: { language: 'javascript' },
    security: { sandbox: true },
    commentable: false,
  },
  {
    id: 'markdown', label: 'Markdown', icon: Hash, tone: '#4f9be0',
    kinds: ['markdown'], exts: ['md', 'markdown', 'mdx'],
    preview: { render: Markdown },
    edit: { language: 'markdown', split: true },
  },
  {
    id: 'document', label: 'Document', icon: ScrollText, tone: '#9d86f5',
    kinds: ['document'],
    preview: { render: DocumentR },
    edit: { language: 'html', split: true },
    security: { sanitize: true },
    generate: { tool: 'artifact_save kind=document', skill: 'editorial-document' },
    exports: [
      { id: 'html', label: 'Download as HTML', run: (c, t) => exportDocumentHtml(c, t) },
      { id: 'copy-html', label: 'Copy HTML', run: (c) => { void copyDocumentHtml(c) } },
    ],
    commentable: true,
  },
  {
    id: 'svg', label: 'SVG', icon: Image, tone: '#3fb950',
    kinds: ['svg'], exts: ['svg'],
    preview: { render: Svg },
    edit: { language: 'xml', split: true },
    security: { sanitize: true },
    commentable: true,
  },
  {
    id: 'infographic', label: 'Infographic', icon: BarChart3, tone: '#5b8cff',
    kinds: ['infographic'],
    preview: { render: Infographic, streaming: true },
    edit: { language: 'plaintext', split: true },
    generate: { tool: 'artifact_save kind=infographic', skill: 'infographic-syntax' },
    exports: [
      { id: 'svg', label: 'Download as SVG', run: (c, t) => exportInfographicSvg(c, t) },
    ],
    commentable: false,
  },
  {
    id: 'json', label: 'JSON', icon: Braces, tone: '#d4a017',
    kinds: ['json'], exts: ['json', 'jsonl', 'json5', 'jsonc'], mimes: ['application/json'],
    preview: { render: JsonTree },
    edit: { language: 'json', split: true },
  },
  {
    id: 'csv', label: 'CSV', icon: Table, tone: '#8a63d2',
    kinds: ['csv'],
    exts: ['csv', 'tsv'], mimes: ['text/csv'],
    preview: { render: CsvTable },
    edit: { language: 'plaintext', split: true },
  },
  {
    id: 'image', label: 'Image', icon: Image, tone: '#3fb950',
    kinds: ['image'],
    exts: ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'ico', 'tiff'],
    mimes: ['image/'],
    preview: { render: ImageFile },
    generate: { tool: 'image_generate' },
    commentable: false,
    binary: true,
  },
  {
    id: 'pdf', label: 'PDF', icon: FileText, tone: '#e0574f',
    kinds: ['pdf'],
    exts: ['pdf'], mimes: ['application/pdf'],
    preview: { render: PdfFile },
    commentable: false,
    binary: true,
  },
  {
    id: 'docx', label: 'Word', icon: FileText, tone: '#2b579a',
    kinds: ['docx'], exts: ['docx'],
    mimes: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    preview: { render: OfficeDocPreview },
    commentable: false,
    binary: true,
  },
  {
    id: 'xlsx', label: 'Spreadsheet', icon: Table, tone: '#1e7145',
    kinds: ['xlsx'], exts: ['xlsx', 'xls'],
    mimes: ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
    preview: { render: OfficeDocPreview },
    commentable: false,
    binary: true,
  },
  {
    id: 'pptx', label: 'Deck', icon: Presentation, tone: '#d24726',
    kinds: ['pptx'], exts: ['pptx'],
    mimes: ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
    preview: { render: OfficeDocPreview },
    commentable: false,
    binary: true,
  },
  {
    id: 'video', label: 'Video', icon: Film, tone: '#d29922',
    kinds: ['video'], exts: ['mp4', 'webm', 'mov'],
    mimes: ['video/'],
    preview: { render: VideoFile },
    commentable: false,
    binary: true,
  },
  {
    id: 'code', label: 'Code', icon: FileCode, tone: 'var(--color-on-surface-low)',
    edit: { language: 'plaintext' },
  },
  {
    id: 'text', label: 'Text', icon: Code2, tone: 'var(--color-on-surface-low)',
    kinds: ['text'], exts: ['txt', 'log'], mimes: ['text/plain'],
    preview: { render: Text },
    edit: { language: 'plaintext' },
  }
] }

let installed = false
export function registerBuiltinContentTypes(): void {
  if (installed) return
  for (const type of builtinDefinitions()) registerContentType(type)
  installed = true
}
export type { LucideIcon }
