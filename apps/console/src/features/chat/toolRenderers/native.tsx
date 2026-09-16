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
import { RawBlock, resolveInputObj } from './primitives'
import type { ToolRenderer } from './registry'

const asObj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {})
const str = (v: unknown): string => (v == null ? '' : String(v))
const inputOf = (seg: ToolSegment): Record<string, unknown> => resolveInputObj(seg) ?? {}


function editFileInput(seg: ToolSegment): ReactNode {
  const o = inputOf(seg)
  const path = str(o.path)
  const oldStr = str(o.old_str)
  const newStr = str(o.new_str)
  if (!oldStr && !newStr) return undefined as unknown as ReactNode
  const diff = oldStr.split('\n').map((l) => `-${l}`).join('\n') + '\n' + newStr.split('\n').map((l) => `+${l}`).join('\n')
  return (
    <div className="mb-1.5">
      <div data-type="caption" className="mb-1 flex items-center gap-1.5 text-on-surface-low"><FilePen size={12} /> <span className="font-mono">{path}</span>{o.replace_all ? <span className="rounded bg-surface-high px-1 py-0.5">replace all</span> : null}</div>
      <RawBlock label="Change"><Markdown>{`\`\`\`diff\n${diff}\n\`\`\``}</Markdown></RawBlock>
    </div>
  )
}

const PRIMARY_INPUT_KEYS = ['path', 'pattern', 'query', 'command', 'url'] as const

function pathChipInput(label: string) {
  return (seg: ToolSegment): ReactNode => {
    const o = inputOf(seg)
    const primary = str(PRIMARY_INPUT_KEYS.map((k) => o[k]).find((v) => v != null && v !== ''))
    if (!primary) return undefined as unknown as ReactNode
    const rest = Object.entries(o).filter(([k]) => !(PRIMARY_INPUT_KEYS as readonly string[]).includes(k))
    return (
      <div className="mb-1.5">
        <div data-type="caption" className="mb-0.5 text-on-surface-low uppercase tracking-wide">{label}</div>
        <code data-type="caption" className="block rounded-md bg-surface-low px-2 py-1.5 font-mono text-on-surface break-words">{primary}</code>
        {rest.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1.5">
            {rest.map(([k, v]) => <span key={k} data-type="caption" className="rounded-pill bg-surface-high px-2 py-0.5 font-mono text-on-surface-low">{k}: {str(v)}</span>)}
          </div>
        )}
      </div>
    )
  }
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
  const url = str(inputOf(seg).url)
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

export const NATIVE_RENDERERS: ToolRenderer[] = [
  { match: (n) => n === 'edit_file', input: editFileInput },
  { match: (n) => n === 'read_file', input: pathChipInput('File') },
  { match: (n) => n === 'write_file', input: pathChipInput('File') },
  { match: (n) => n === 'glob' || n === 'list_dir', input: pathChipInput('Pattern'), output: hitListOutput },
  { match: (n) => n === 'grep', input: pathChipInput('Query'), output: hitListOutput },
  { match: (n) => n === 'bash', input: pathChipInput('Command'), output: diffOutput },
  { match: (n) => n === 'knowledge_search', output: searchResultsOutput },
  { match: (n) => n === 'web_search' || n === 'web', output: searchResultsOutput },
  { match: (n) => n === 'web_fetch', input: pathChipInput('URL'), output: webFetchOutput },
  { match: (n) => n === 'task_create' || n === 'task_update', output: taskChipOutput },
  { match: (n) => n.startsWith('memory_'), output: memoryChipOutput },
  { match: (n) => n === 'project_run_create' || n === 'project_run_status', output: runStatusOutput },
]

function bareName(name: string): string {
  if (!name.startsWith('mcp__')) return name.toLowerCase()
  const parts = name.split('__').filter(Boolean)
  return (parts.length >= 3 ? parts.slice(2).join('_') : name).toLowerCase()
}


const ICON_BY_NAME: Record<string, LucideIcon> = {
  read_file: FileText, write_file: FilePlus, edit_file: FilePen,
  list_dir: List, glob: Search, grep: Search, repo_map: FolderInput,
  bash: Terminal,
  knowledge_search: BookOpen, knowledge_create: BookOpen, knowledge_get: BookOpen,
  knowledge_update: BookOpen, knowledge_stats: BookOpen,
  task_create: ListChecks, task_get: ListChecks, task_list: ListChecks,
  task_update: ListChecks, task_search: ListChecks, task_ready: ListChecks,
  task_list_create: ListChecks, project_create: FolderInput, project_list: FolderInput,
  project_run_create: Bot, project_run_start: Bot, project_run_status: ListChecks,
  project_run_list: ListChecks,
  web_search: Globe, web_fetch: Globe, memory_recall: Brain, memory_remember: Brain,
  tool_result_get: FileText, post_to_inbox: MessageSquare,
}

const _BY_KIND: Record<string, LucideIcon> = {
  execute: Terminal, read: FileText, edit: FilePen, delete: Trash2,
  move: FolderInput, search: Search, fetch: Globe, think: Brain, other: Wrench,
}

export function iconForTool(seg: ToolSegment): LucideIcon {
  const name = bareName(seg.tool || '')
  if (ICON_BY_NAME[name]) return ICON_BY_NAME[name]
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

const LABEL_BY_NAME: Record<string, string> = {
  read_file: 'Read', write_file: 'Write', edit_file: 'Edit', list_dir: 'List',
  glob: 'Find files', grep: 'Search code', repo_map: 'Map repo',
  bash: 'Run command',
  knowledge_search: 'Search Knowledge', knowledge_create: 'Add Knowledge',
  knowledge_get: 'Get Knowledge', knowledge_update: 'Update Knowledge',
  knowledge_stats: 'Knowledge stats',
  task_create: 'Create task', task_get: 'Get task', task_list: 'List tasks',
  task_update: 'Update task', task_search: 'Search tasks', task_ready: 'Ready tasks',
  task_list_create: 'Create task list', project_create: 'Create project',
  project_list: 'List projects',
  project_run_create: 'Create project run', project_run_start: 'Start project run',
  project_run_status: 'Project status', project_run_list: 'List project runs',
  web_search: 'Web search', web_fetch: 'Fetch page', memory_recall: 'Recall',
  memory_remember: 'Remember', tool_result_get: 'Fetch full result',
  post_to_inbox: 'Notify',
}
export function labelForTool(seg: ToolSegment): string {
  const raw = seg.tool || ''
  const known = LABEL_BY_NAME[bareName(raw)]
  if (known) return known
  return humanize(bareName(raw)) || 'Tool'
}

function humanize(name: string): string {
  const words = name.replace(/[_-]+/g, ' ').trim()
  if (!words) return ''
  return words.charAt(0).toUpperCase() + words.slice(1)
}
