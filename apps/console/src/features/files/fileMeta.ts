import {
  FileText, FileCode, Image, FileJson, Table, Globe, File as FileIcon,
  Folder, Box, Code2, Hash, Braces, BarChart3, ScrollText, ImagePlus,
  Presentation, Film, type LucideIcon,
} from 'lucide-react'
import type { ArtifactKind } from '../../shared/data/api'

export type FileViewType = 'image' | 'pdf' | 'csv' | 'json' | 'html' | 'markdown' | 'code'

const IMG_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp', 'svg', 'ico', 'tiff'])

const LANG_MAP: Record<string, string> = {
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', mts: 'typescript', cts: 'typescript', tsx: 'typescript',
  py: 'python', pyi: 'python', pyw: 'python',
  sh: 'shell', bash: 'shell', zsh: 'shell', ksh: 'shell', fish: 'shell',
  html: 'html', htm: 'html', xhtml: 'html', vue: 'html', svelte: 'html',
  css: 'css', scss: 'scss', sass: 'scss', less: 'less',
  json: 'json', json5: 'json', jsonc: 'json', geojson: 'json', webmanifest: 'json', jsonl: 'json',
  yaml: 'yaml', yml: 'yaml',
  toml: 'toml', ini: 'ini', cfg: 'ini', conf: 'ini', editorconfig: 'ini', properties: 'ini',
  xml: 'xml', svg: 'xml', xsl: 'xml', xsd: 'xml', plist: 'xml', csproj: 'xml', rss: 'xml',
  md: 'markdown', markdown: 'markdown', mdx: 'markdown', rst: 'restructuredtext',
  c: 'cpp', h: 'cpp', cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp', hxx: 'cpp', ino: 'cpp',
  rs: 'rust', go: 'go',
  java: 'java', kt: 'kotlin', kts: 'kotlin', scala: 'scala', sc: 'scala',
  groovy: 'java', gradle: 'java', clj: 'clojure', cljs: 'clojure', cljc: 'clojure',
  cs: 'csharp', fs: 'fsharp', fsx: 'fsharp', vb: 'vb', razor: 'razor', cshtml: 'razor',
  rb: 'ruby', rake: 'ruby',
  php: 'php', php3: 'php', php4: 'php', php5: 'php', phtml: 'php',
  swift: 'swift', m: 'objective-c', mm: 'objective-c',
  dart: 'dart', lua: 'lua', pl: 'perl', pm: 'perl',
  r: 'r', jl: 'julia', ex: 'elixir', exs: 'elixir',
  sql: 'sql', mysql: 'mysql', pgsql: 'pgsql', psql: 'pgsql', graphql: 'graphql', gql: 'graphql',
  dockerfile: 'dockerfile', containerfile: 'dockerfile',
  tf: 'hcl', tfvars: 'hcl', hcl: 'hcl', nomad: 'hcl',
  ps1: 'powershell', psm1: 'powershell', psd1: 'powershell',
  bat: 'bat', cmd: 'bat', proto: 'protobuf', sol: 'solidity', wgsl: 'wgsl', tcl: 'tcl',
  hbs: 'handlebars', handlebars: 'handlebars', mustache: 'handlebars',
  pug: 'pug', jade: 'pug', twig: 'twig', liquid: 'liquid',
  coffee: 'coffeescript', bicep: 'bicep',
  sv: 'systemverilog', svh: 'systemverilog',
  scm: 'scheme', ss: 'scheme', rkt: 'scheme',
  pas: 'pascal', pp: 'pascal',
  csv: 'plaintext', tsv: 'plaintext', env: 'shell', txt: 'plaintext', log: 'plaintext',
}

const FILENAME_MAP: Record<string, string> = {
  dockerfile: 'dockerfile', containerfile: 'dockerfile', makefile: 'shell',
  'cmakelists.txt': 'cpp', gemfile: 'ruby', rakefile: 'ruby', procfile: 'yaml', vagrantfile: 'ruby',
  '.gitignore': 'plaintext', '.gitattributes': 'plaintext', '.dockerignore': 'plaintext',
  '.npmrc': 'ini', '.editorconfig': 'ini', '.env': 'shell',
  '.bashrc': 'shell', '.zshrc': 'shell', '.bash_profile': 'shell', '.profile': 'shell',
}

export function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i + 1).toLowerCase() : ''
}

export function fileViewType(name: string): FileViewType {
  const ext = extOf(name)
  if (IMG_EXTS.has(ext) && ext !== 'svg') return 'image'
  if (ext === 'svg') return 'image'
  if (ext === 'pdf') return 'pdf'
  if (ext === 'csv' || ext === 'tsv') return 'csv'
  if (ext === 'json' || ext === 'jsonl') return 'json'
  if (ext === 'html' || ext === 'htm') return 'html'
  if (ext === '' && monacoLang(name) !== 'plaintext') return 'code'
  // bucket (READMEs, NOTES, LICENSE) after the code-filename guard above has claimed
  if (['md', 'markdown', 'mdx', ''].includes(ext)) return 'markdown'
  return 'code'
}

export function monacoLang(name: string): string {
  const base = (name.split('/').pop() || name).toLowerCase()
  if (FILENAME_MAP[base]) return FILENAME_MAP[base]
  const ext = extOf(base)
  if (LANG_MAP[ext]) return LANG_MAP[ext]
  if (base === '.env' || base.startsWith('.env.')) return 'shell'
  if (base.startsWith('dockerfile.') || base.endsWith('.dockerfile')) return 'dockerfile'
  if (base.startsWith('docker-compose') || base.startsWith('compose.')) return 'yaml'
  return 'plaintext'
}

export function fileIcon(name: string, isDir: boolean): LucideIcon {
  if (isDir) return Folder
  const t = fileViewType(name)
  if (t === 'image') return Image
  if (t === 'json') return FileJson
  if (t === 'csv') return Table
  if (t === 'html') return Globe
  if (t === 'markdown') return FileText
  if (t === 'code') return ['txt', 'log'].includes(extOf(name)) ? FileText : FileCode
  return FileIcon
}

export function gitBadge(code: string | undefined): { label: string; tone: string } | null {
  if (!code) return null
  const c = code.trim()
  if (c === '??') return { label: 'U', tone: 'var(--color-on-surface-low)' }
  if (c.includes('U') || c === 'AA' || c === 'DD') return { label: '!', tone: 'var(--color-danger)' }
  if (c.includes('D')) return { label: 'D', tone: 'var(--color-danger)' }
  if (c.includes('A')) return { label: 'A', tone: 'var(--color-ok)' }
  if (c.includes('R')) return { label: 'R', tone: 'var(--color-primary)' }
  if (c.includes('C')) return { label: 'C', tone: 'var(--color-primary)' }
  return { label: 'M', tone: 'var(--color-warn)' }
}

export function gitStatusTitle(code: string | undefined): string {
  if (!code) return ''
  const c = code.trim()
  if (c === '??') return 'Untracked (new file)'
  if (c.includes('U') || c === 'AA' || c === 'DD') return `Merge conflict (${c})`
  if (c.includes('D')) return 'Deleted'
  if (c.includes('A')) return 'Added'
  if (c.includes('R')) return 'Renamed'
  if (c.includes('C')) return 'Copied'
  return 'Modified'
}

export const ARTIFACT_KINDS: { key: ArtifactKind; label: string; icon: LucideIcon; tone: string }[] = [
  { key: 'widget', label: 'Widget', icon: Box, tone: 'var(--color-primary)' },
  { key: 'html', label: 'HTML', icon: Globe, tone: '#e06c4f' },
  { key: 'react', label: 'React', icon: Box, tone: '#61dafb' },
  { key: 'markdown', label: 'Markdown', icon: Hash, tone: '#4f9be0' },
  { key: 'svg', label: 'SVG', icon: Image, tone: '#3fb950' },
  { key: 'infographic', label: 'Infographic', icon: BarChart3, tone: '#5b8cff' },
  { key: 'document', label: 'Document', icon: ScrollText, tone: '#9d86f5' },
  { key: 'image', label: 'Image', icon: ImagePlus, tone: '#3fb950' },
  { key: 'json', label: 'JSON', icon: Braces, tone: '#d4a017' },
  { key: 'text', label: 'Text', icon: Code2, tone: 'var(--color-on-surface-low)' },
  { key: 'csv', label: 'CSV', icon: Table, tone: '#3fb950' },
  { key: 'docx', label: 'Word', icon: FileText, tone: '#4f9be0' },
  { key: 'xlsx', label: 'Spreadsheet', icon: Table, tone: '#3fb950' },
  { key: 'pptx', label: 'Slides', icon: Presentation, tone: '#e06c4f' },
  { key: 'pdf', label: 'PDF', icon: FileText, tone: '#e05c5c' },
  { key: 'video', label: 'Video', icon: Film, tone: '#9d86f5' },
]

export const UNKNOWN_ARTIFACT_KIND: { key: ''; label: string; icon: LucideIcon; tone: string } = {
  key: '', label: 'Unknown kind', icon: FileIcon, tone: 'var(--color-on-surface-low)',
}

export function artifactKindMeta(kind: string) {
  return ARTIFACT_KINDS.find((k) => k.key === kind) ?? UNKNOWN_ARTIFACT_KIND
}

export function fmtBytes(n?: number): string {
  if (n == null) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / (1024 * 1024)).toFixed(1)} MB`
}

export function relTime(iso: string | number): string {
  if (!iso) return ''
  const t = typeof iso === 'number' ? iso * (iso < 1e12 ? 1000 : 1) : Date.parse(iso)
  if (Number.isNaN(t)) return ''
  const s = Math.floor((Date.now() - t) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  if (s < 604800) return `${Math.floor(s / 86400)}d ago`
  return new Date(t).toLocaleDateString()
}

export function baseName(path: string): string {
  return path.replace(/\/+$/, '').split('/').pop() || path
}
