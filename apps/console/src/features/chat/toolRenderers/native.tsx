import { MoreRow } from '../../../shared/ui/MoreRow'
import { type ReactNode } from 'react'
import {
  Wrench, Terminal, FileText, FilePen, FilePlus, Search, Globe, Bot, List,
  Trash2, FolderInput, Brain, BookOpen, ListChecks, Database, GitBranch,
  MessageSquare, type LucideIcon,
} from 'lucide-react'
import { Markdown } from '../../../shared/ui/Markdown'
import { fvs } from '../../../shared/theme/fontWeight'
import type { ToolSegment } from '../chatTypes'
import { RawBlock, inputOf } from './primitives'
import type { ToolRenderer } from './registry'

const asObj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {})
const str = (v: unknown): string => (v == null ? '' : String(v))
const resolvedInput = (seg: ToolSegment): Record<string, unknown> => inputOf(seg) ?? {}


function editFileInput(seg: ToolSegment): ReactNode {
  const o = resolvedInput(seg)
  const path = str(o.path)
  const oldStr = str(o.old_str)
  const newStr = str(o.new_str)
  if (!oldStr && !newStr) return undefined as unknown as ReactNode
  const diff = oldStr.split('\n').map((l) => `-${l}`).join('\n') + '\n' + newStr.split('\n').map((l) => `+${l}`).join('\n')
  return (
    <div className="mb-1.5">
      <div data-type="caption" className="mb-1 flex items-center gap-1.5 text-on-surface-low"><FilePen size={12} /> <span className="font-mono">{path}</span>{o.replace_all ? <span className="rounded bg-surface-high px-1 py-0.5">replace all</span> : null}</div>
      <RawBlock label={nativeRendererForTool(seg.tool)?.inputLabel ?? 'Input'}><Markdown>{`\`\`\`diff\n${diff}\n\`\`\``}</Markdown></RawBlock>
    </div>
  )
}

const PRIMARY_INPUT_KEYS = ['path', 'pattern', 'query', 'command', 'url'] as const

function pathChipInput(seg: ToolSegment): ReactNode {
    const o = resolvedInput(seg)
    const primary = str(PRIMARY_INPUT_KEYS.map((k) => o[k]).find((v) => v != null && v !== ''))
    if (!primary) return undefined as unknown as ReactNode
    const rest = Object.entries(o).filter(([k]) => !(PRIMARY_INPUT_KEYS as readonly string[]).includes(k))
    return (
      <div className="mb-1.5">
        <div data-type="caption" className="mb-0.5 text-on-surface-low uppercase tracking-wide">{nativeRendererForTool(seg.tool)?.inputLabel}</div>
        <code data-type="caption" className="block rounded-md bg-surface-low px-2 py-1.5 font-mono text-on-surface break-words">{primary}</code>
        {rest.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {rest.map(([k, v]) => <span key={k} data-type="caption" className="rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-low">{k}: {str(v)}</span>)}
          </div>
        )}
      </div>
    )
}


function hitListOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text || text.startsWith('{') || text.startsWith('[')) return undefined as unknown as ReactNode
  const lines = text.split('\n').filter(Boolean)
  if (lines.length === 0 || lines.length > 500) return undefined as unknown as ReactNode
  return (
    <RawBlock label="Matches">
      <div data-type="caption" className="flex flex-col font-mono">
        {lines.slice(0, 300).map((ln, i) => {
          const m = ln.match(/^(.+?):(\d+):(.*)$/)
          return m
            ? <div key={i} className="flex gap-2 py-0.5"><span className="shrink-0 text-primary">{m[1]}</span><span className="shrink-0 text-on-surface-low tabular-nums">{m[2]}</span><span className="truncate text-on-surface-var">{m[3]}</span></div>
            : <div key={i} className="py-0.5 text-on-surface-var">{ln}</div>
        })}
        <MoreRow total={lines.length} shown={300} className="mt-1" />
      </div>
    </RawBlock>
  )
}

function diffOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!/^(diff --git |@@ |index [0-9a-f]|--- |\+\+\+ )/m.test(text)) return undefined as unknown as ReactNode
  return <RawBlock label="Diff"><Markdown>{`\`\`\`diff\n${text}\n\`\`\``}</Markdown></RawBlock>
}

function searchResultsOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  let data: unknown
  try { data = JSON.parse(text) } catch { return undefined as unknown as ReactNode }
  const arr = Array.isArray(data) ? data : (asObj(data).results as unknown[]) || (asObj(data).items as unknown[])
  if (!Array.isArray(arr) || arr.length === 0) return undefined as unknown as ReactNode
  return (
    <RawBlock label={`${arr.length} result${arr.length === 1 ? '' : 's'}`}>
      <div className="flex flex-col gap-1.5">
        {arr.slice(0, 20).map((r, i) => {
          const o = asObj(r)
          const title = str(o.title || o.name || o.url || o.id)
          const sub = str(o.url || o.type || o.snippet || o.description)
          return (
            <div key={i} className="rounded-md bg-surface px-2 py-1.5">
              <div data-type="label-s" className="truncate text-on-surface" style={fvs(550)}>{title}</div>
              {sub && sub !== title && <div data-type="caption" className="truncate text-on-surface-low">{sub}</div>}
            </div>
          )
        })}
        <MoreRow total={arr.length} shown={20} />
      </div>
    </RawBlock>
  )
}

function webFetchOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text || text.startsWith('{') || text.startsWith('[')) return undefined as unknown as ReactNode
  const url = str(resolvedInput(seg).url)
  return (
    <RawBlock label="Fetched page">
      {url && <div data-type="caption" className="mb-1 truncate text-primary">{url}</div>}
      <div className="max-h-72 overflow-auto"><Markdown>{text.slice(0, 8000)}</Markdown></div>
    </RawBlock>
  )
}

function taskChipOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text) return undefined as unknown as ReactNode
  let title = '', status = '', id = ''
  try {
    const d = asObj(JSON.parse(text))
    title = str(d.title || d.subject); status = str(d.status); id = str(d.id || d.task_id)
  } catch {
    const m = text.match(/\b(t-[\w-]+)\b/)
    if (!m) return undefined as unknown as ReactNode
    id = m[1]; title = text.replace(/^[^:]*:\s*/, '').slice(0, 120)
  }
  if (!id && !title) return undefined as unknown as ReactNode
  return (
    <div className="mt-1 inline-flex flex-wrap items-center gap-1.5 rounded-md bg-surface-container px-2 py-1">
      <ListChecks size={12} className="text-primary" />
      {id && <span data-type="caption" className="font-mono text-on-surface-low">{id}</span>}
      {title && <span data-type="caption" className="text-on-surface">{title}</span>}
      {status && <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 text-on-surface-var uppercase">{status}</span>}
    </div>
  )
}

function memoryChipOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text || text.length > 240 || text.startsWith('{') || text.startsWith('[')) return undefined as unknown as ReactNode
  if (/^(error|failed|declined|refus|could ?n.?t|unable|no (memory|such|matching)|not found|invalid)\b/i.test(text)) {
    return undefined as unknown as ReactNode
  }
  return (
    <div className="mt-1 flex items-start gap-1.5 rounded-md bg-surface-container px-2 py-1">
      <Brain size={12} className="mt-0.5 shrink-0 text-primary" />
      <span data-type="caption" className="text-on-surface-var">{text}</span>
    </div>
  )
}

function runStatusOutput(seg: ToolSegment): ReactNode {
  const text = (seg.output ?? '').trim()
  if (!text) return undefined as unknown as ReactNode
  let id = '', status = ''
  try { const d = asObj(JSON.parse(text)); id = str(d.id || d.loop_id); status = str(d.status || d.state) } catch {   }
  if (!id) { const m = text.match(/\b([0-9a-f]{8})\b/); if (m) id = m[1] }
  if (!id && !status) return undefined as unknown as ReactNode
  return (
    <div className="mt-1 inline-flex items-center gap-1.5 rounded-md bg-surface-container px-2 py-1">
      <Bot size={12} className="text-primary" />
      {id && <span data-type="caption" className="font-mono text-on-surface-low">{id}</span>}
      {status && <span data-type="caption" className="rounded-pill bg-surface-high px-1.5 text-on-surface-var uppercase">{status}</span>}
    </div>
  )
}

function bareName(name: string): string {
  if (!name.startsWith('mcp__')) return name.toLowerCase()
  const parts = name.split('__').filter(Boolean)
  return (parts.length >= 3 ? parts.slice(2).join('_') : name).toLowerCase()
}


type NativeTool = ToolRenderer & { icon: LucideIcon }

export const NATIVE_TOOL_REGISTRY: Record<string, NativeTool> = {
  read_file: { label: 'Read', icon: FileText, inputLabel: 'File', input: pathChipInput },
  write_file: { label: 'Write', icon: FilePlus, inputLabel: 'File', input: pathChipInput },
  edit_file: { label: 'Edit', icon: FilePen, inputLabel: 'Change', input: editFileInput },
  list_dir: { label: 'List', icon: List, inputLabel: 'Pattern', input: pathChipInput, output: hitListOutput },
  glob: { label: 'Find files', icon: Search, inputLabel: 'Pattern', input: pathChipInput, output: hitListOutput },
  grep: { label: 'Search code', icon: Search, inputLabel: 'Query', input: pathChipInput, output: hitListOutput },
  repo_map: { label: 'Map repo', icon: FolderInput },
  bash: { label: 'Run command', icon: Terminal, inputLabel: 'Command', input: pathChipInput, output: diffOutput },
  knowledge_search: { label: 'Search Knowledge', icon: BookOpen, output: searchResultsOutput },
  knowledge_create: { label: 'Add Knowledge', icon: BookOpen },
  knowledge_get: { label: 'Get Knowledge', icon: BookOpen },
  knowledge_update: { label: 'Update Knowledge', icon: BookOpen },
  knowledge_stats: { label: 'Knowledge stats', icon: BookOpen },
  task_create: { label: 'Create task', icon: ListChecks, output: taskChipOutput },
  task_get: { label: 'Get task', icon: ListChecks }, task_list: { label: 'List tasks', icon: ListChecks },
  task_update: { label: 'Update task', icon: ListChecks, output: taskChipOutput },
  task_search: { label: 'Search tasks', icon: ListChecks }, task_ready: { label: 'Ready tasks', icon: ListChecks },
  task_list_create: { label: 'Create task list', icon: ListChecks },
  project_create: { label: 'Create project', icon: FolderInput }, project_list: { label: 'List projects', icon: FolderInput },
  project_run_create: { label: 'Create project run', icon: Bot, output: runStatusOutput },
  project_run_start: { label: 'Start project run', icon: Bot },
  project_run_status: { label: 'Project status', icon: ListChecks, output: runStatusOutput },
  project_run_list: { label: 'List project runs', icon: ListChecks },
  web_search: { label: 'Web search', icon: Globe, output: searchResultsOutput },
  web: { label: 'Web', icon: Globe, output: searchResultsOutput },
  web_fetch: { label: 'Fetch page', icon: Globe, inputLabel: 'URL', input: pathChipInput, output: webFetchOutput },
  memory_recall: { label: 'Recall', icon: Brain, output: memoryChipOutput },
  memory_remember: { label: 'Remember', icon: Brain, output: memoryChipOutput },
  tool_result_get: { label: 'Fetch full result', icon: FileText }, post_to_inbox: { label: 'Notify', icon: MessageSquare },
}

export function nativeRendererForTool(tool: string): NativeTool | undefined {
  const name = bareName(tool || '')
  return NATIVE_TOOL_REGISTRY[name] ?? (name.startsWith('memory_')
    ? { label: humanize(name), icon: Brain, output: memoryChipOutput }
    : undefined)
}

const _BY_KIND: Record<string, LucideIcon> = {
  execute: Terminal, read: FileText, edit: FilePen, delete: Trash2,
  move: FolderInput, search: Search, fetch: Globe, think: Brain, other: Wrench,
}

export function iconForTool(seg: ToolSegment): LucideIcon {
  const name = bareName(seg.tool || '')
  const native = nativeRendererForTool(name)
  if (native) return native.icon
  if (seg.toolKind && _BY_KIND[seg.toolKind]) return _BY_KIND[seg.toolKind]
  if (/terminal|bash|shell|exec|command|run/.test(name)) return Terminal
  if (/edit|write|patch|create|str_?replace/.test(name)) return FilePen
  if (/read|cat|view|open/.test(name)) return FileText
  if (/grep|search|find|glob/.test(name)) return Search
  if (/fetch|web|http|url|browse/.test(name)) return Globe
  if (/list|ls|dir/.test(name)) return List
  if (/agent|task|subagent|delegate|dispatch/.test(name)) return Bot
  if (/memor|recall|lesson/.test(name)) return Brain
  if (/knowledge/.test(name)) return BookOpen
  if (/delete|remove|rm/.test(name)) return Trash2
  if (/git/.test(name)) return GitBranch
  if (/database|memory|store/.test(name)) return Database
  return Wrench
}

export function labelForTool(seg: ToolSegment): string {
  const raw = seg.tool || ''
  const known = nativeRendererForTool(raw)?.label
  if (known) return known
  return humanize(bareName(raw)) || 'Tool'
}

function humanize(name: string): string {
  const words = name.replace(/[_-]+/g, ' ').trim()
  if (!words) return ''
  return words.charAt(0).toUpperCase() + words.slice(1)
}
