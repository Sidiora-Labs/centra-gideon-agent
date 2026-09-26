import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ResultAnnouncement } from '../shared/ui/ListControls'
import { reportActionFailure, reportingWrite } from '../app/shell/reportingWrite'
import { unavailableWhen, BUSY_REASON } from '../shared/ui/unavailable'

interface VoiceLoopConfig {
  confirmation_phrases: string[]
  exit_phrases: string[]
  duplex_mute_enabled: boolean
}
const DEFAULT_CONFIRMATION_PHRASES = ['do it', 'go ahead', 'send it', 'execute']
const DEFAULT_EXIT_PHRASES = ['cancel', 'never mind', 'forget it']
import { fvs, withWeight } from '../shared/theme/fontWeight'
import { playCue } from '../shared/theme/soundCues'
import { motion, AnimatePresence } from 'framer-motion'
import { Edit3, History, Search, MessageSquare, Trash2, Activity, ChevronRight, ChevronDown, Quote, PanelRight, Clipboard, X, Pin, FileText, BookText, AlertTriangle, Pencil, Sparkles, Link2, Check, Repeat, Rewind, PlayCircle, GitBranch, Folder, FolderPlus, Tag as TagIcon, Columns3, List as ListIcon, ListChecks, Filter, EyeOff, Clock, Loader2, Wrench, Target, Code2 as CodeIcon, Paperclip, ExternalLink, ArrowLeft, ArrowRight, ArrowUp, FolderKanban, GripVertical, MessageCircleQuestion, Bot, ShieldCheck, Shield, Eye, Zap, ClipboardList, Hammer, Camera, NotebookPen, FolderCog, Archive, ArchiveRestore, Boxes, CornerDownLeft, Download, Share2, Coins } from 'lucide-react'
import { IconButton } from '../shared/ui/IconButton'
import { SquareIconButton } from '../shared/ui/SquareIconButton'
import { SearchField } from '../shared/ui/SearchField'
import { TopBar } from '../shared/ui/TopBar'
import { SidePanel } from '../shared/ui/SidePanel'
import { Button } from '../shared/ui/Button'
import { Checkbox } from '../shared/ui/forms'
import { QuietButton } from '../shared/ui/QuietButton'
import { SelectionToolbar } from '../shared/ui/SelectionPill'
import { Segmented } from '../shared/ui/Segmented'
import { Meter } from '../shared/ui/Meter'
import { ContextMenu, type ContextMenuItem } from '../shared/ui/motion'
import { ProjectPicker } from '../shared/ui/ProjectPicker'
import { HeaderActions, HeaderControl, HeaderSegmented, HeaderModePill } from '../shared/ui/HeaderActions'
import { GideonMark } from '../shared/ui/GideonMark'
import { ComposerStage } from '../shared/ui/ComposerStage'
import { CollapseColumnButton, CollapsedBoardColumn, boardGridTemplate, useBoardCollapse } from '../shared/ui/BoardCollapse'
import { PromptPalette } from './chat/PromptPalette'
import { SessionSkillsReview } from './chat/SessionSkillsReview'
import { RoutingChip, type RoutingSuggestion } from './chat/RoutingChip'
import { starterPrefill, type StarterPrefill } from './chat/starterPrefill'
import { OrganizeChip } from './chat/OrganizeChip'
import { ContextLedger } from './chat/ContextLedger'
import { ScreenShareChip } from '../shared/ui/ScreenShareChip'
import { useScreenShare } from '../shared/ui/composer/useScreenShare'
import { DotGlow } from '../shared/ui/DotGlow'
import { EmptyState, ListSkeleton, LoadError, Skeleton, LoadingStatus } from '../shared/ui/ListScaffold'
import { WindowedList } from '../shared/ui/WindowedList'
import { FieldError } from '../shared/ui/forms'
import { MessageUser } from '../shared/ui/chat/MessageUser'
import { MessageAssistant } from '../shared/ui/chat/MessageAssistant'
import { Spark } from '../shared/ui/Spark'
import { StreamingIndicator } from '../shared/ui/chat/StreamingIndicator'
import { ChatPlanGate } from '../shared/ui/chat/ChatPlanGate'
import { Markdown } from '../shared/ui/Markdown'
import { useWidgetActionBridge, takePendingWidgetAction } from '../shared/ui/widget/useWidgetActionBridge'
import { InlineError } from '../shared/ui/InlineError'
import { NoModelSetupState, isNoModelSetupError, MODELS_PATH } from './chat/NoModelSetupState'
import { ToolCard } from './chat/ToolCard'
import { onToolResultFull } from './chat/toolResultBridge'
import { SdlcProgressCard, sdlcRefFromTool } from './chat/SdlcProgressCard'
import { WorkflowProgressCard, workflowRefFromTool } from './chat/WorkflowProgressCard'
import { ApprovalCard } from './chat/ApprovalCard'
import { ChatFilePanel } from './chat/ChatFilePanel'
import { sameSessionTarget, type CommentTarget } from '../shared/ui/content/commentTarget'
import { SessionWorkspace } from './chat/SessionWorkspace'
import { createScrollToTurnHandler } from './chat/scrollToTurn'
import { AssistantActions, UserActions } from './chat/MessageActions'
import { parseOptions, parseSwitchToAgent } from './chat/parseAssistant'
import { type PasteBlock, shouldCollapsePaste, makePasteId, markerFor, expandPasteMarkers, pruneBlocks } from './chat/pasteBlocks'
import { Modal } from '../shared/ui/Modal'
import { confirm, promptInput } from '../shared/ui/dialog'
import { type ChatTurn, type Segment, type ToolSegment, type ApprovalSegment, type ActivitySegment, type ThinkingSegment, appendThinking, type SubagentCard, type HistMsg, type MemoryCitation, type SkillUsed, userTurn, assistantTurn, hydrateTurns, turnText, deriveActivity, skillsUsedLabel, skillsUsedTitle, stampActivityOrigin } from './chat/chatTypes'
import { ThinkingBlock } from './chat/ThinkingBlock'
import { branchIndexOf, branchParentKey } from './chat/branchLineage'
import { buildOptimizerContext } from './chat/optimizerContext'
import { useIdentity, firstNameOf } from '../app/shell/identity'
import { usePlatform } from '../app/shell/usePlatform'
import { SnipOverlay } from '../shared/ui/SnipOverlay'
import { chooseCaptureProvider, cropToPngFile, displayCaptureSupported, grabOneFrame, type SnipRect } from '../shared/ui/composer/displayCapture'
import { notify } from '../app/shell/appSdk'
import { spring, stagger, listItemEnter, expr, useReducedMotion } from '../shared/theme/motion'
import { api, type ApprovalMode, type TaskMode, type ReasoningEffort, type ChatSessionSummary, type ChatHistoryMsg, type DiscoveredAgent, type MemoryMode, type NudgeLoop, type ChatFolder, type ChatTag, type RetagJob, type RewindFileWire } from '../shared/data/api'
import { useChatSocket, type WsMessage } from '../shared/data/useChatSocket'
import { useStreamCoalescer } from './chat/useStreamCoalescer'
import { FindBar } from '../shared/ui/FindBar'
import { findSegments } from './chat/findSegments'
import { FollowupChips, followupAnnouncement } from './chat/FollowupChips'
import { CheckWorkChip } from './chat/CheckWorkChip'
import { SessionMarkerRail } from './chat/SessionMarkerRail'
import { applyCoalescedFlush, insertActivity } from './chat/coalesceReducers'
import { useQuery, invalidateKeys, peekQuery, writeQuery } from '../shared/data/data'
import { sessionRecencyMs, sessionActivitySeconds, epochSeconds } from '../shared/data/epoch'
import { sessionTitle } from '../shared/data/sessionTitle'
import { useComposerData } from '../shared/data/useComposerData'
import type { ComposerControls, ComposerValue } from '../shared/ui/composer/types'
import { Popover, MenuRow } from '../shared/ui/Popover'
import { useQueryFlag, useQueryParam, type RouteProps } from '../app/shell/useQueryState'
import { copyText } from '../app/shell/clipboard'
import { MoreRow } from '../shared/ui/MoreRow'

type ChatDetail = Awaited<ReturnType<typeof api.chatSessionDetail>>
const detailKey = (key: string) => `chat:detail:${key}`
const readCachedDetail = (key: string): ChatDetail | null => peekQuery<ChatDetail>(detailKey(key)) ?? null
function writeCachedDetail(key: string, d: ChatDetail): void {
  if (d.running) return
  writeQuery(detailKey(key), d, true)
}

type ApproveAction = 'approved' | 'rejected' | 'revised' | 'trust' | 'trust_agent' | 'trust_reads' | 'yolo'

const MEMORY_MODES: { id: MemoryMode; label: string; hint: string }[] = [
  { id: 'persistent', label: 'Persistent', hint: 'Remember across sessions' },
  { id: 'temporary', label: 'Temporary', hint: 'Forget when the session ends' },
  { id: 'incognito', label: 'Incognito', hint: 'Do not write to memory' },
]

const APPROVAL_SLIDER = [
  { key: 'normal', label: 'Normal', icon: Shield, title: 'Normal — ask before every tool' },
  { key: 'trust_reads', label: 'Trust reads', icon: Eye, title: 'Trust reads — auto-approve read-only tools' },
  { key: 'trust', label: 'Trust', icon: ShieldCheck, title: 'Trust — auto-approve every tool in this chat' },
  { key: 'yolo', label: 'YOLO', icon: Zap, title: 'YOLO — auto-approve everywhere; auto-expires, re-enable to extend' },
]

const TASK_MODE_SLIDER = [
  { key: 'agent', label: 'Agent', icon: Bot, title: 'Agent — full execution (default)' },
  { key: 'ask', label: 'Ask', icon: MessageSquare, title: 'Ask — read-only Q&A; mutating tools are blocked' },
  { key: 'plan', label: 'Plan', icon: ClipboardList, title: 'Plan — plan the work without executing any tool' },
  { key: 'build', label: 'Build', icon: Hammer, title: 'Build — scoped to producing an artifact / widget / skill' },
]

function greeting(name: string): string {
  const h = new Date().getHours()
  const part = h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening'
  return `Good ${part}, ${firstNameOf(name)}`
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`
  return String(n)
}

function SuggestionChips({ onPick }: { onPick: (s: string) => void }) {
  const { data } = useQuery('chat:suggestions', () => api.suggestions().then((r) => r.suggestions), { persist: true })
  const items = (data ?? []).slice(0, 6)
  if (!items.length) return null
  return (
    <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
      {items.map((s, i) => (
        <motion.button key={i} type="button" onClick={() => onPick(s)}
          initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: 0.04 * i }}
          className="rounded-pill border border-outline-variant/60 bg-surface-container px-3.5 py-2 text-left text-[0.8125rem] text-on-surface-var transition-colors hover:border-primary/40 hover:bg-surface-high hover:text-on-surface">
          {s}
        </motion.button>
      ))}
      <MoreRow total={data?.length ?? 0} shown={6} noun="suggestions" />
    </div>
  )
}

function StarterChips({ onPick }: { onPick: (prefill: StarterPrefill) => void }) {
  const { data } = useQuery('chat:starters', () => api.sessionTemplates(), { persist: true })
  const items = (data ?? []).slice(0, 6)
  if (!items.length) return null
  return (
    <div className="flex w-full flex-col items-center gap-2">
      <p className="text-[0.75rem] text-on-surface-low">Your starters</p>
      <div className="flex flex-wrap justify-center gap-2" style={{ maxWidth: 720 }}>
        {items.map((t, i) => (
          <motion.button key={t.id} type="button" onClick={() => {
            const prefill = starterPrefill(t.id, items)
            if (prefill) onPick(prefill)
          }}
            title={t.first_prompt || t.name}
            initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ ...spring.spatialDefault, delay: 0.04 * i }}
            className="flex items-center gap-2 rounded-pill border border-primary/30 bg-primary-container/30 px-3.5 py-2 text-left text-[0.8125rem] text-on-surface-var transition-colors hover:border-primary/60 hover:bg-primary-container/50 hover:text-on-surface">
            <Sparkles size={13} className="shrink-0 text-primary" />
            {t.name}
          </motion.button>
        ))}
      </div>
      <MoreRow total={data?.length ?? 0} shown={6} noun="starters" />
    </div>
  )
}

function relTimeShort(at?: string | number): string {
  const at_s = epochSeconds(at)
  if (at_s == null) return ''
  const s = Math.max(0, Date.now() / 1000 - at_s)
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 86400) return `${Math.floor(s / 3600)}h`
  if (s < 604800) return `${Math.floor(s / 86400)}d`
  return `${Math.floor(s / 604800)}w`
}

function ChatHistorySidePanelBody({ navigate, onOpen }: { navigate: (p: string) => void; onOpen: (key: string) => void }) {
  const { data, error: sessionsError, refresh: refreshSessions } = useQuery<ChatSessionSummary[]>('chat:sessions', () => api.chatSessions(), { persist: false })
  const recent = useMemo(() => {
    const all = data ?? []
    return all
      .filter((s) => (s.origin ?? 'manual') === 'manual')
      .sort((a, b) => sessionRecencyMs(b) - sessionRecencyMs(a))
      .slice(0, 20)
  }, [data])
  const manualCount = (data ?? []).filter((s) => (s.origin ?? 'manual') === 'manual').length
  return (
    <div className="flex flex-col gap-1">
      {data === undefined && sessionsError ? (
        <LoadError what="chats" error={sessionsError} onRetry={refreshSessions} />
      ) : data === undefined ? (
        <div className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">Loading…</div>
      ) : recent.length === 0 ? (
        <div className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">No chats yet.</div>
      ) : (
        <motion.div variants={{ animate: { transition: stagger(0.03) } }} initial="initial" animate="animate" className="flex flex-col gap-0.5">
           {recent.map((s) => (
            <motion.button key={s.key} type="button" variants={listItemEnter} onClick={() => onOpen(s.key)}
              whileHover={{ x: expr(3, 0.3) }} transition={spring.spatialFast}
              className="group flex items-center gap-s rounded-md px-2 py-2 text-left transition-colors hover:bg-surface-high">
              <MessageSquare size={14} className="shrink-0 text-on-surface-low group-hover:text-primary transition-colors" />
              <span className="min-w-0 flex-1 truncate text-on-surface-var text-[0.8125rem] group-hover:text-on-surface">{sessionTitle(s)}</span>
              <span className="shrink-0 text-on-surface-low text-[0.75rem] tabular-nums">{relTimeShort(sessionActivitySeconds(s))}</span>
            </motion.button>
           ))}
           <MoreRow total={manualCount} shown={20} noun="chats" className="px-2 py-1" />
        </motion.div>
      )}
      { }
      <button type="button" onClick={() => navigate('chat/history')}
        className="mt-1 flex items-center justify-center gap-1.5 rounded-md border border-outline-variant/40 px-2 py-2 text-on-surface-var text-[0.8125rem] transition-colors hover:bg-surface-high hover:text-on-surface"
        style={fvs(470)}>
        View all chats <ArrowRight size={13} className="shrink-0" />
      </button>
    </div>
  )
}

function SessionPeekBody({ sessionKey, onOpen }: { sessionKey: string; onOpen: () => void }) {
  const [detail, setDetail] = useState<{ title: string; messages: ChatHistoryMsg[] } | null>(null)
  const [failed, setFailed] = useState(false)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [streamText, setStreamText] = useState<string | null>(null)
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    let alive = true
    setDetail(null); setFailed(false); setStreamText(null); setBusy(false)
    api.chatSessionDetail(sessionKey)
      .then((d) => { if (alive) setDetail({ title: d.title, messages: d.messages ?? [] }) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [sessionKey])

  useChatSocket((m) => {
    const d = m.data || {}
    if (d.session !== sessionKey) return
    if (m.type === 'chat_chunk') {
      setStreamText((t) => (t ?? '') + String(d.content ?? ''))
    } else if (m.type === 'chat_done') {
      setBusy(false)
      setStreamText((t) => {
        if (t) setDetail((prev) => prev && { ...prev, messages: [...prev.messages, { role: 'assistant', content: t } as ChatHistoryMsg] })
        return null
      })
    }
  })
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [detail?.messages.length, streamText])

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput(''); setBusy(true)
    setDetail((prev) => prev && { ...prev, messages: [...prev.messages, { role: 'user', content: text } as ChatHistoryMsg] })
    try { await api.sendChat(text, sessionKey) }
    catch { setBusy(false); notify('Could not send the message', 'error') }
  }

  if (failed) return <p className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">Couldn't load this chat.</p>
  if (!detail) return <ListSkeleton rows={5} />

  const shown = detail.messages.filter((m) => m.role === 'user' || m.role === 'assistant').slice(-12)
  return (
    <div className="flex h-full min-h-0 flex-col gap-m">
      <div className="flex min-h-0 flex-1 flex-col gap-m overflow-y-auto">
        {shown.length === 0 && !streamText ? (
          <p className="px-2 py-6 text-center text-on-surface-low text-[0.8125rem]">No messages yet — say hi below.</p>
        ) : shown.map((m, i) => (
          m.role === 'user' ? (
            <div key={i} className="ml-6 self-end rounded-lg bg-surface-high px-m py-s">
              <p className="whitespace-pre-wrap break-words text-on-surface text-[0.8125rem] leading-relaxed">{String(m.content || '').slice(0, 800)}</p>
            </div>
          ) : (
            <div key={i} className="mr-2 min-w-0 text-[0.8125rem]">
              <Markdown className="[&_p]:text-[0.8125rem]">{parseSwitchToAgent(parseOptions(String(m.content || '').slice(0, 2000)).body).body}</Markdown>
            </div>
          )
        ))}
        { }
        {streamText && (
          <div className="mr-2 min-w-0 text-[0.8125rem]">
            <Markdown className="[&_p]:text-[0.8125rem]">{streamText}</Markdown>
          </div>
        )}
        {busy && !streamText && (
          <div className="flex items-center gap-s">
            <Spark size={16} />
            <motion.span className="text-on-surface-low text-[0.8125rem]" animate={{ opacity: [0.5, 1, 0.5] }} transition={{ duration: 1.8, ease: 'easeInOut', repeat: Infinity }}>Thinking…</motion.span>
          </div>
        )}
        <div ref={endRef} />
      </div>
      {
}
      <div className="shrink-0 rounded-lg bg-surface-container p-s shadow-[var(--shadow-composer)]">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="Quick reply…"
          rows={2}
          aria-label="Quick reply"
          className="w-full resize-none bg-transparent px-s py-xs text-on-surface text-[0.8125rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary placeholder:text-on-surface-low"
        />
        <div className="flex items-center gap-s">
          <Button variant="ghost" size="xs" onClick={onOpen} title="Open the full chat UI"
            className="gap-1 text-on-surface-low hover:text-on-surface">
            Continue in full chat <ArrowRight size={11} className="shrink-0" />
          </Button>
          <motion.button type="button" onClick={send}
            {...unavailableWhen(!input.trim(), 'Type a message first', { busy })}
            whileTap={{ scale: 0.92 }} transition={spring.spatialFast}
            aria-label="Send"
            className="ml-auto inline-flex size-8 shrink-0 items-center justify-center rounded-pill bg-primary text-on-primary transition-colors hover:bg-primary-emphasis disabled:opacity-40 disabled:pointer-events-none aria-disabled:opacity-40 aria-disabled:cursor-not-allowed">
            {busy ? <Loader2 size={14} className="animate-spin" /> : <ArrowUp size={14} />}
          </motion.button>
        </div>
      </div>
    </div>
  )
}

const CHAT_CONTROLS: ComposerControls = {
  agent: true, model: true, approval: false, reasoning: true,
  attach: true, mic: true, optimize: true, slash: true,
}

const SLASH_HELP = [
  '## Slash commands',
  '',
  'These run instantly in the dashboard — they don’t go to the model:',
  '',
  '- `/help` — show this list',
  '- `/optimize <prompt>` — optimize the prompt, then send the optimized version',
  '- `/clear` — start a fresh chat',
  '- `/prompts` — open the saved-prompt palette (`/prompts <name>` invokes one directly)',
  '- `/model` — switch the model for this chat',
  '- `/agent` — switch the agent for this chat',
  '- `/effort` — set the reasoning effort for this chat',
  '- `/project` — scope this new chat to a project (before it starts)',
  '- `/tools` — open the Tools page',
  '- `/undo [N]` — roll back the last N conversation turns (default 1; side effects are not reverted)',
  '- `/rewind-to-turn N` — restore the FILES this chat changed after turn N (preview first; add `--confirm` to apply). The conversation is not rewound.',
  '- `/compact` — compact the conversation to free up context',
  '',
  'Type `/` in the message box any time to see and filter the full list.',
].join('\n')


export function ChatPage({ sub, navigate, navEpoch = 0, query, setQuery }: { sub: string; navigate: (p: string, opts?: { replace?: boolean }) => void; navEpoch?: number; query?: Record<string, string>; setQuery?: RouteProps['setQuery'] }) {
  const seg = (sub || '').split('/')[0]
  const projectId = query?.project || ''
  const seed = query?.seed || ''
  const agentParam = query?.agent || ''
  const q = query ?? {}
  const setQ: RouteProps['setQuery'] = setQuery ?? (() => {})
  if (projectId && (!seg || seg === 'new')) return <ChatSession key={`new-proj-${projectId}-${navEpoch}`} sessionId={null} navigate={navigate} query={q} setQuery={setQ} projectId={projectId} seed={seed} agent={agentParam} />
  if (seg === 'history') return <ChatHistoryPage navigate={navigate} query={q} setQuery={setQ} />
  if (!seg || seg === 'new') return <ChatSession key={`new-${navEpoch}`} sessionId={null} navigate={navigate} query={q} setQuery={setQ} seed={seed} agent={agentParam} />
  return <ChatSession key={sub} sessionId={sub} navigate={navigate} query={q} setQuery={setQ} seed={seed} />
}

function ChatSession({ sessionId, navigate, query, setQuery, projectId: initialProjectId = '', seed = '', agent: initialAgent = '' }: { sessionId: string | null; navigate: (p: string, opts?: { replace?: boolean }) => void; query: Record<string, string>; setQuery: RouteProps['setQuery']; projectId?: string; seed?: string; agent?: string }) {
  const data = useComposerData()
  const { name } = useIdentity()
  const [projectId, setProjectId] = useState(initialProjectId)
  useEffect(() => { setProjectId(initialProjectId) }, [initialProjectId])
  const [projectName, setProjectName] = useState('')
  useEffect(() => {
    if (!projectId) { setProjectName(''); return }
    let alive = true
    api.project(projectId).then((p) => { if (alive) setProjectName(p?.name || '') }).catch(() => {})
    return () => { alive = false }
  }, [projectId])
  const [sessionCost, setSessionCost] = useState<{ cost: number; tokens: number; priced: boolean } | null>(null)
  const refreshSessionCost = useCallback((key: string | null) => {
    if (!key) { setSessionCost(null); return }
    const ledgerSessionKey = key.includes(':') ? key : `dashboard:${key}`
    api.usageTotals({ session: ledgerSessionKey }).then((d) => {
      const t = d.totals
      const tokens = (t.input_tokens || 0) + (t.output_tokens || 0)
      setSessionCost(t.turns > 0 && tokens > 0 ? { cost: t.cost_usd, tokens, priced: t.priced } : null)
    }).catch(() => {   })
  }, [])
  const seededDetail = useRef<ChatDetail | null>(sessionId ? readCachedDetail(sessionId) : null).current
  const [turns, setTurns] = useState<ChatTurn[]>(
    () => (seededDetail ? hydrateTurns(seededDetail.messages || [], false) : []),
  )
  const [input, setInput] = useState(seed)
  const [streaming, setStreaming] = useState(false)
  const streamingRef = useRef(false)
  const [sessionSkillsEpoch, setSessionSkillsEpoch] = useState(0)
  const markStreaming = (v: boolean) => {
    if (streamingRef.current && !v) {
      setSessionSkillsEpoch((n) => n + 1)
      playCue('turn_complete')
    }
    streamingRef.current = v
    setStreaming(v)
  }
  const [composerFocused, setComposerFocused] = useState(false)
  const [promptPaletteOpen, setPromptPaletteOpen] = useState(false)
  const [openModelSignal, setOpenModelSignal] = useState(0)
  const [openAgentSignal, setOpenAgentSignal] = useState(0)
  const [openReasoningSignal, setOpenReasoningSignal] = useState(0)
  const [openProjectSignal, setOpenProjectSignal] = useState(0)
  const [loadingHistory, setLoadingHistory] = useState(!!sessionId && !seededDetail)
  const composerRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const [scrolledUp, setScrolledUp] = useState(false)
  const [wsConnected, setWsConnected] = useState(true)
  const glowTargetRef = useRef<HTMLDivElement | null>(null)
  const glowAnchorRef = useRef<HTMLDivElement | null>(null)
  const [activityOpen, setActivityOpen] = useQueryFlag(query, setQuery, 'activity')
  const [workspacePane, setWorkspacePane] = useQueryParam(query, setQuery, 'workspace', '')
  const [historyOpen, setHistoryOpen] = useQueryFlag(query, setQuery, 'history')
  const turnNodes = useRef<Map<number, HTMLDivElement>>(new Map())
  const [findOpen, setFindOpen] = useState(false)
  useEffect(() => { setFindOpen(false) }, [sessionId])
  const [followups, setFollowups] = useState<string[]>([])
  useEffect(() => { setFollowups([]) }, [sessionId])
  const [checkWorkOffer, setCheckWorkOffer] = useState<{ label: string; prompt: string } | null>(null)
  useEffect(() => { setCheckWorkOffer(null) }, [sessionId])
  const [routingSuggestion, setRoutingSuggestion] = useState<RoutingSuggestion | null>(null)
  useEffect(() => { setRoutingSuggestion(null) }, [sessionId])
  const [mentionedFiles, setMentionedFiles] = useState<string[]>([])
  const [mentionedKnowledge, setMentionedKnowledge] = useState<{ id: string; name: string }[]>([])
  const [knowledgePickerOpen, setKnowledgePickerOpen] = useState(false)

  const [mentionedArtifacts, setMentionedArtifacts] = useState<{ slug: string; name: string }[]>([])
  const [artifactPickerOpen, setArtifactPickerOpen] = useState(false)
  const [pasteBlocks, setPasteBlocks] = useState<PasteBlock[]>([])
  const pasteSeq = useRef(0)
  const [attachedPaths, setAttachedPaths] = useState<string[]>([])
  const platform = usePlatform()
  const displayCapture = displayCaptureSupported()
  const captureProvider = chooseCaptureProvider(platform, displayCapture)
  const [snip, setSnip] = useState<{ url: string; width: number; height: number; source: HTMLCanvasElement } | null>(null)
  const [uploads, setUploads] = useState<{ name: string; pct: number }[]>([])
  const uploadAbortRef = useRef<AbortController | null>(null)
  const [promptHistory, setPromptHistory] = useState<string[]>([])
  const [contextPct, setContextPct] = useState<number | undefined>(undefined)
  const [optimizing, setOptimizing] = useState(false)
  const [preOptimize, setPreOptimize] = useState<string | null>(null)
  const [micError, setMicError] = useState<string | null>(null)
  const screenShare = useScreenShare(sessionId ?? '', (m) => {
    setMicError(m)
    window.setTimeout(() => setMicError(null), 6000)
  })
  const [toast, setToast] = useState<string | null>(null)

  const [attachError, setAttachError] = useState<string | null>(null)
  const [memoryMode, setMemoryMode] = useState<MemoryMode>('persistent')
  const [branchedFrom, setBranchedFrom] = useState<{ key: string; title: string } | null>(null)
  const [investigateOrigin, setInvestigateOrigin] = useState<import('../shared/data/api').InvestigateOrigin | null>(null)
  const [resultRef, setResultRef] = useQueryParam(query, setQuery, 'result', '')
  const resultToolRef = useRef<string>('')
  const [resultBody, setResultBody] = useState<{ content: string; length: number } | null>(null)
  const [queued, setQueued] = useState<{ id: string; content: string }[]>([])
  const [steered, setSteered] = useState<string[]>([])
  const [subagents, setSubagents] = useState<SubagentCard[]>([])
  const [sideMsgs, setSideMsgs] = useState<{ q: string; a: string; runId: string; done: boolean }[]>([])
  const [sideBusy, setSideBusy] = useState(false)
  const sideOpenedRef = useRef(false)

  const sessionRef = useRef<string | null>(sessionId)
  const ensureInFlightRef = useRef<Promise<string> | null>(null)
  const lastWsActivityRef = useRef<number>(0)
  const [selection, setSelection] = useState<ComposerValue>({ agent: initialAgent, model: 'Auto', approval: 'normal', taskMode: 'agent', reasoning: '' })
  const sessionBindingRef = useRef<{ agent: string; model: string; acp_provider: string; acp_provider_agent: string; reasoning_effort: string } | null>(null)
  const [bindingNonce, setBindingNonce] = useState(0)
  const [naturalVoice, setNaturalVoice] = useState<{ choice: '' | 'on' | 'off'; effective: boolean; source: string; agentDefault: boolean }>(
    { choice: '', effective: false, source: '', agentDefault: false })
  const [statusText, setStatusText] = useState('')
  const [latestActivity, setLatestActivity] = useState<string | null>(null)
  const [openFileRaw, setOpenFileRaw] = useQueryParam(query, setQuery, 'file', '')
  const openFile = openFileRaw || null
  const setOpenFile = (p: string | null) => setOpenFileRaw(p || '')
  const [title, setTitle] = useState(seededDetail?.title || '')
  const [renaming, setRenaming] = useState(false)
  const [renameVal, setRenameVal] = useState('')
  const [linkCopied, setLinkCopied] = useState(false)
  const [regenningTitle, setRegenningTitle] = useState(false)
  const breakText = useRef(false)
  const coalescing = useRef(false)
  const { data: streamRevealCfg } = useQuery('chat:stream-reveal', () => api.dashboardConfig().then((c) => c.stream_reveal), { persist: true })
  const { data: showThinkingCfg } = useQuery('chat:show-thinking-inline', () => api.dashboardConfig().then((c) => c.show_thinking_inline), { persist: true })
  const { data: showTimestamps } = useQuery('chat:show-timestamps', () => api.dashboardConfig().then((c) => c.show_timestamps), { persist: true })
  const stampOf = (turn: { ts?: string }) => (showTimestamps ? turn.ts : undefined)
  const showThinkingRef = useRef(false)
  useEffect(() => { showThinkingRef.current = !!showThinkingCfg }, [showThinkingCfg])
  const coalescer = useStreamCoalescer((revealed) => {
    patchLastAssistant((segs) => {
      const r = applyCoalescedFlush(segs, revealed, coalescing.current)
      coalescing.current = r.coalescing
      return r.segs
    })
  }, { immediate: streamRevealCfg === 'immediate' })
  const started = turns.length > 0
  const lastTurn = turns[turns.length - 1]
  const showThinking = streaming && (!lastTurn || lastTurn.role === 'user' || lastTurn.segments.length === 0)

  const [srAnnounce, setSrAnnounce] = useState('')
  const wasStreamingRef = useRef(false)
  useEffect(() => {
    if (streaming && !wasStreamingRef.current) setSrAnnounce(statusText || 'Assistant is responding…')
    else if (!streaming && wasStreamingRef.current) setSrAnnounce('Response complete.')
    else if (streaming && statusText) setSrAnnounce(statusText)
    wasStreamingRef.current = streaming
  }, [streaming, statusText])

  const ensureAssistant = (list: ChatTurn[]): ChatTurn[] =>
    (list.length && list[list.length - 1].role === 'assistant') ? list : [...list, assistantTurn()]
  const patchLastAssistant = (fn: (segs: Segment[]) => Segment[]) =>
    setTurns((prev) => {
      const list = ensureAssistant(prev)
      const i = list.length - 1
      const next = [...list]
      next[i] = { ...next[i], segments: fn([...next[i].segments]) }
      return next
    })

  useEffect(() => {
    sessionRef.current = sessionId
    coalescer.reset(); coalescing.current = false
    setQueued([])
    setSubagents([])
    setBranchedFrom(null)
    if (!sessionId) { setTurns([]); setLoadingHistory(false); return }
    let alive = true
    if (!seededDetail) setLoadingHistory(true)
    api.chatSessionDetail(sessionId).then((d) => {
      if (!alive) return
      writeCachedDetail(sessionId, d)
      const hydrated = hydrateTurns(d.messages || [], d.running)
      setTurns((prev) => (hydrated.length >= prev.length ? hydrated : prev))
      setQueued(Array.isArray(d.queue) ? d.queue.filter((q) => q && q.id).map((q) => ({ id: q.id, content: q.content })) : [])
      setPromptHistory(hydrated.reduce<string[]>((acc, t) => {
        if (t.role !== 'user') return acc
        const txt = turnText(t).trim()
        if (txt && acc[acc.length - 1] !== txt) acc.push(txt)
        return acc
      }, []).slice(-50))
      setTitle(d.title || '')
      refreshSessionCost(sessionId)
      setSelection((s) => ({
        ...s,
        taskMode: (d.task_mode || 'agent') as TaskMode,
        approval: (d.approval || 'normal') as ApprovalMode,
      }))
      setMemoryMode((d.memory_mode || 'persistent') as MemoryMode)
      setNaturalVoice({
        choice: ((d.natural_voice || '') as '' | 'on' | 'off'),
        effective: !!d.natural_voice_effective,
        source: d.natural_voice_source || '',
        agentDefault: !!d.natural_voice_agent_default,
      })
      setBranchedFrom(d.forked_from
        ? { key: branchParentKey(d.forked_from), title: d.forked_from_title || '' }
        : null)
      setInvestigateOrigin((d as { investigate?: import('../shared/data/api').InvestigateOrigin | null }).investigate ?? null)
      sessionBindingRef.current = {
        agent: d.agent || '', model: d.model || '',
        acp_provider: d.acp_provider || '', acp_provider_agent: d.acp_provider_agent || '',
        reasoning_effort: d.reasoning_effort || '',
      }
      setBindingNonce((n) => n + 1)
      if (d.side?.messages?.length) {
        const pairs: { q: string; a: string; runId: string; done: boolean }[] = []
        for (const m of d.side.messages) {
          if (m.role === 'user') pairs.push({ q: m.content, a: '', runId: '', done: true })
          else if (pairs.length) pairs[pairs.length - 1].a += m.content
        }
        setSideMsgs(pairs)
        sideOpenedRef.current = true
      }
      markStreaming(!!d.running)
      if (d.running) breakText.current = true
      setLoadingHistory(false)
    }).catch(() => { if (alive) setLoadingHistory(false) })
    return () => { alive = false }
  }, [sessionId])

  useEffect(() => {
    const b = sessionBindingRef.current
    if (!b) return
    const reasoning = (b.reasoning_effort || '') as ReasoningEffort
    if (b.acp_provider) {
      const list = data.discovered?.[b.acp_provider] ?? []
      const match = list.find((a) => a.provider_agent === b.acp_provider_agent)
      if (match) setSelection((s) => ({ ...s, agent: match.name, model: b.model || 'Auto', reasoning }))
    } else if (b.agent || b.model || reasoning) {
      setSelection((s) => ({ ...s, agent: b.agent, model: b.model || 'Auto', reasoning }))
    }
  }, [bindingNonce, data.discovered])

  const onWs = useCallback((m: WsMessage) => {
    const s = sessionRef.current
    const d = m.data || {}
    if (!s || (d.session !== s && d.session !== undefined)) return
    lastWsActivityRef.current = Date.now()
    switch (m.type) {
      case 'chat_chunk': {
        if (d.session !== sessionRef.current) break
        setStatusText('')
        const chunk = String(d.content ?? '')
        if (breakText.current) { coalescer.reset(); coalescing.current = false; breakText.current = false }
        coalescer.push(chunk)
        break
      }
      case 'chat_status': setStatusText(String(d.status ?? '')); break
      case 'chat_thinking': {
        if (!showThinkingRef.current) break
        const chunk = String(d.content ?? '')
        if (!chunk) break
        coalescer.flushNow()
        patchLastAssistant((segs) => appendThinking(segs, chunk))
        breakText.current = true
        break
      }
      case 'chat_message': {
        if (d.session && d.session !== sessionRef.current) break
        if (d.role === 'error') {
          coalescer.flushNow()
          markStreaming(false); setStatusText(''); setLatestActivity(null)
          patchLastAssistant((segs) => [...segs, { kind: 'error', text: String(d.content ?? 'The model returned an error.') }])
        }
        break
      }
      case 'activity_event': {
        const kind = String(d.kind ?? '')
        const text = String(d.text ?? '')
        if (kind === 'status' || kind === 'session' || !text) break
        setLatestActivity(text)
        const origin = String(d.origin ?? '')
        patchLastAssistant((segs) => stampActivityOrigin(segs, insertActivity(segs, text, kind, coalescing.current), origin))
        break
      }
      case 'tool_call': {
        coalescer.flushNow()
        const id = String(d.tool_call_id ?? '')
        patchLastAssistant((segs) => {
          const existing = segs.find((sg) => sg.kind === 'tool' && sg.id === id) as ToolSegment | undefined
          if (existing) {
            if (d.input_preview) existing.input = String(d.input_preview)
            if (d.input !== undefined && d.input !== null) existing.inputObj = d.input
            if (d.detail) existing.detail = String(d.detail)
            if (d.tool && !d.update) existing.tool = String(d.tool)
            return [...segs]
          }
          segs.push({ kind: 'tool', id, tool: String(d.tool ?? 'tool'), detail: d.detail ? String(d.detail) : undefined,
            toolKind: String(d.kind ?? ''), input: String(d.input_preview ?? ''),
            inputObj: (d.input !== undefined && d.input !== null) ? d.input : undefined,
            purpose: String(d.purpose ?? ''), auto: !!d.auto, done: false })
          return segs
        })
        breakText.current = true
        break
      }
      case 'tool_result':
        patchLastAssistant((segs) => segs.map((sg) =>
          sg.kind === 'tool' && sg.id === String(d.tool_call_id ?? '')
            ? { ...sg, output: String(d.output ?? ''), done: true,
                contentType: d.content_type ? String(d.content_type) : sg.contentType,
                rawRef: d.raw_ref ? String(d.raw_ref) : sg.rawRef,
                truncated: d.truncated != null ? !!d.truncated : sg.truncated,
                originalLength: d.original_length != null ? Number(d.original_length) : sg.originalLength,
                recoveryHints: Array.isArray(d.recovery_hints) && d.recovery_hints.length
                  ? (d.recovery_hints as string[]) : sg.recoveryHints,
                agentError: d.agent_error ? (d.agent_error as ToolSegment['agentError']) : sg.agentError,
                ok: d.ok === false ? false : sg.ok }
            : sg))
        break
      case 'approval':
        coalescer.flushNow()
        patchLastAssistant((segs) => {
          const id = String(d.id ?? '')
          if (segs.some((sg) => sg.kind === 'approval' && sg.id === id)) return segs
          segs.push({ kind: 'approval', id, tool: String(d.tool ?? 'tool'), toolKind: String(d.tool_kind ?? ''), canRevise: d.can_revise === true, input: String(d.tool_input ?? ''), purpose: String(d.tool_purpose ?? ''), risk: (d.risk ? String(d.risk) : undefined) as ApprovalSegment['risk'] })
          return segs
        })
        breakText.current = true
        break
      case 'approval_resolved':
        setTurns((prev) => prev.map((t) => ({ ...t, segments: t.segments.map((sg) =>
          sg.kind === 'approval' && sg.id === String(d.id ?? '') ? { ...sg, resolved: d.approved ? String(d.decision ?? 'approved') : 'rejected' } as ApprovalSegment : sg) })))
        break
      case 'chat_segment': coalescer.flushNow(); breakText.current = true; break
      case 'chat_variant_switch': {
        if (d.session !== sessionRef.current) break
        const content = String(d.content ?? '')
        const index = typeof d.index === 'number' ? (d.index as number) : 0
        const count = typeof d.count === 'number' ? (d.count as number) : undefined
        setTurns((prev) => {
          const i = prev.map((t) => t.role).lastIndexOf('assistant')
          if (i < 0) return prev
          const next = [...prev]
          next[i] = { ...next[i], segments: [{ kind: 'text', text: content }], variantIdx: index,
            variantCount: count ?? next[i].variantCount }
          return next
        })
        break
      }
      case 'context_usage':
        if (d.session === sessionRef.current) setContextPct(typeof d.pct === 'number' ? d.pct : undefined)
        break
      case 'session_title': {
        const key = String(d.key ?? '')
        const t = String(d.title ?? '')
        if (key && t && key === sessionRef.current) setTitle(t)
        break
      }
      case 'chat_done': {
        coalescer.flushNow()
        breakText.current = true; markStreaming(false); setStatusText(''); setLatestActivity(null)
        setSteered([])

        if (d.superseded) setStatusText('Superseded by your new message…')
        const sk = sessionRef.current
        refreshSessionCost(sk)
        if (sk) api.chatSessionDetail(sk).then((d) => {
          writeCachedDetail(sk, d)
          if (sk !== sessionRef.current) return
          coalescer.reset(); coalescing.current = false; breakText.current = true
          setTurns(hydrateTurns(d.messages || [], d.running))
          markStreaming(!!d.running)
        }).catch(() => {})
        break
      }
      case 'queue_push': {
        if (d.session !== sessionRef.current) break
        const id = String(d.queue_id ?? ''); const content = String(d.content ?? '')
        if (id) setQueued((prev) => (prev.some((q) => q.id === id) ? prev : [...prev, { id, content }]))
        break
      }
      case 'queue_pop':
      case 'queue_cancel': {
        if (d.session !== sessionRef.current) break
        const id = String(d.queue_id ?? '')
        if (id) setQueued((prev) => prev.filter((q) => q.id !== id))
        break
      }
      case 'queue_promoted': {
        if (d.session !== sessionRef.current) break
        const id = String(d.queue_id ?? '')
        if (id) setQueued((prev) => {
          const i = prev.findIndex((q) => q.id === id)
          if (i <= 0) return prev
          const next = [...prev]; next.unshift(next.splice(i, 1)[0]); return next
        })
        break
      }
      case 'chat_followups': {
        if (d.session !== sessionRef.current) break
        const items = Array.isArray(d.items) ? d.items.filter((x): x is string => typeof x === 'string') : []
        setFollowups(items)
        break
      }
      case 'chat_check_work_offer': {
        if (d.session !== sessionRef.current) break
        const prompt = String(d.prompt ?? 'check your work')
        setCheckWorkOffer({ label: String(d.label ?? 'Check this work'), prompt })
        break
      }
      case 'routing_suggestion': {
        if (d.session !== sessionRef.current) break
        const agent = String(d.agent ?? '')
        if (!agent) break
        setRoutingSuggestion({
          session: String(d.session), agent,
          specialty: String(d.specialty ?? ''),
          score: typeof d.score === 'number' ? d.score : 0,
          method: String(d.method ?? ''),
        })
        break
      }
      case 'chat_rewound': {
        if (d.session !== sessionRef.current) break
        const sk = sessionRef.current
        if (sk) api.chatSessionDetail(sk).then((det) => {
          writeCachedDetail(sk, det)
          setTurns(hydrateTurns(det.messages || [], det.running))
        }).catch(() => {})
        break
      }
      case 'chat_user_message': {
        if (d.session !== sessionRef.current) break
        const content = String(d.content ?? '')
        if (!content) break
        breakText.current = true
        setFollowups([])
        setTurns((prev) => [...prev, userTurn(content, d.ts ? String(d.ts) : undefined)])
        break
      }
      case 'subagent_spawn': {
        if (d.session !== sessionRef.current) break
        const id = String(d.id ?? '')
        if (!id) break
        setSubagents((prev) => prev.some((s) => s.id === id) ? prev
          : [...prev, { id, task: String(d.task ?? ''), agent: String(d.agent ?? ''), done: false }])
        break
      }
      case 'subagent_tool': {
        if (d.session !== sessionRef.current) break
        const id = String(d.id ?? '')
        if (id) setSubagents((prev) => prev.map((s) => s.id === id ? { ...s, lastTool: String(d.tool ?? '') } : s))
        break
      }
      case 'subagent_done': {
        if (d.session !== sessionRef.current) break
        const id = String(d.id ?? '')
        if (id) setSubagents((prev) => prev.map((s) => s.id === id
          ? { ...s, done: true, error: (d.error as string | null) ?? null, elapsed: typeof d.elapsed === 'number' ? d.elapsed : undefined, result: String(d.result ?? ''),
              costUsd: typeof d.cost_usd === 'number' ? d.cost_usd : undefined, tokens: typeof d.tokens === 'number' ? d.tokens : undefined,
              memoryReceipt: d.memory_receipt && typeof d.memory_receipt === 'object' ? d.memory_receipt as SubagentCard['memoryReceipt'] : undefined }
          : s))
        break
      }
      case 'voice_chunk': if (d.audio) enqueueAudio(String(d.audio)); break
      case 'chat.side_result': {
        const rid = String(d.run_id ?? '')
        const delta = String(d.delta ?? '')
        const done = !!d.done
        setSideMsgs((prev) => {
          let idx = prev.findIndex((m) => m.runId === rid)
          if (idx < 0) idx = prev.map((m) => !m.done).lastIndexOf(true)
          if (idx < 0) return prev
          const next = [...prev]
          next[idx] = { ...next[idx], runId: next[idx].runId || rid, a: next[idx].a + delta, done: done || next[idx].done }
          return next
        })
        if (done) setSideBusy(false)
        break
      }
    }
  }, [])
  const resyncOnReconnect = useCallback(() => {
    const s = sessionRef.current
    if (!s) return
    api.chatSessionDetail(s).then((d) => {
      if (sessionRef.current !== s) return
      coalescer.reset(); coalescing.current = false; breakText.current = true
      setTurns(hydrateTurns(d.messages || [], d.running))
      markStreaming(!!d.running)
      if (!d.running) setStatusText('')
    }).catch(() => {})
  }, [])
  useChatSocket(onWs, resyncOnReconnect, setWsConnected)

  useEffect(() => {
    if (!streaming) return
    const iv = window.setInterval(() => {
      const s = sessionRef.current
      if (!s) return
      if (Date.now() - lastWsActivityRef.current < 3500) return
      const showingApproval = turns.some((t) => t.segments.some((sg) => sg.kind === 'approval' && !(sg as ApprovalSegment).resolved))
      if (showingApproval) return
      api.chatSessionDetail(s).then((d) => {
        if (sessionRef.current !== s) return
        coalescer.flushNow()
        coalescer.reset(); coalescing.current = false; breakText.current = true
        setTurns(hydrateTurns(d.messages || [], d.running))
        if (!d.running) { markStreaming(false); setStatusText(''); setLatestActivity(null) }
        lastWsActivityRef.current = Date.now()
      }).catch(() => {})
    }, 2000)
    return () => window.clearInterval(iv)
  }, [streaming, turns])

  // Global "/" shortcut → focus the composer (GitHub/Slack-style), unless the
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      const cm = composerRef.current?.querySelector<HTMLElement>('.cm-content')
      if (cm) { e.preventDefault(); cm.focus() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'f' || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey) return
      if (!sessionRef.current) return
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) {
        const inFind = !!t.closest('[role="search"]')
        if (!inFind) return
      }
      e.preventDefault()
      setFindOpen((o) => !o)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useWidgetActionBridge((text, meta) => { void send(text, { uiLabel: meta.label }) })

  useEffect(() => {
    const pending = takePendingWidgetAction()
    if (pending) void send(pending.text, { uiLabel: pending.label })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const approve = useCallback((id: string, action: ApproveAction, revision?: string) => {
    const s = sessionRef.current
    if (!s) return
    const raised: ApprovalMode | null =
      action === 'trust' || action === 'trust_agent' ? 'trust'
      : action === 'trust_reads' ? 'trust_reads'
      : action === 'yolo' ? 'yolo'
      : null
    api.approve(s, action, id, revision)
      .then((result) => {
        const screening = result.approval_screening
        if (screening?.verdict === 'denied') {
          if (result.mode) setSelection((sel) => ({ ...sel, approval: result.mode! }))
          notify(screening.reason, 'warning')
        } else if (raised) {
          setSelection((sel) => (sel.approval === raised ? sel : { ...sel, approval: raised }))
        }
      })
      .catch(reportActionFailure('record your decision'))
  }, [])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 200
    if (nearBottom) endRef.current?.scrollIntoView({ block: 'end' })
  }, [turns, streaming, showThinking])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onScroll = () => setScrolledUp(el.scrollHeight - el.scrollTop - el.clientHeight > 240)
    onScroll()
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [started])

  useEffect(() => {
    setPasteBlocks((prev) => (prev.length ? pruneBlocks(input, prev) : prev))
  }, [input])

  useEffect(() => {
    glowTargetRef.current = streaming ? glowAnchorRef.current : null
  }, [streaming, turns])

  useEffect(() => onToolResultFull(({ rawRef, tool }) => {
    resultToolRef.current = tool
    setResultRef(rawRef)
  }), []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!resultRef) { setResultBody(null); return }
    const sess = sessionRef.current
    if (!sess) return
    let alive = true
    setResultBody(null)
    fetch(`/api/chat/sessions/${encodeURIComponent(sess)}/tool-result/${encodeURIComponent(resultRef)}`,
      { headers: { 'X-Session-Key': 'dashboard:ui' } })
      .then(async (res) => {
        if (res.ok) return res.json()
        const reason = await res.json().catch(() => null)
        throw new Error(reason?.error || `HTTP ${res.status}`)
      })
      .then((d) => { if (alive) setResultBody({ content: String(d.content ?? ''), length: Number(d.length ?? 0) }) })
      .catch((e) => { if (alive) setResultBody({ content: `(couldn't load the full result: ${String(e?.message || e)})`, length: 0 }) })
    return () => { alive = false }
  }, [resultRef])

  async function ensureSession(seedMessages?: HistMsg[], navigateNow = true): Promise<string> {
    if (sessionRef.current) return sessionRef.current
    if (ensureInFlightRef.current) return ensureInFlightRef.current
    const p = (async () => {
      const acp = acpFor(selection.agent)
      const created = await api.createChatSession({
        agent: acp ? undefined : (selection.agent || undefined),
        model: acp ? undefined : (selection.model && selection.model !== 'Auto' ? selection.model : undefined),
        memory_mode: memoryMode !== 'persistent' ? memoryMode : undefined,
        project_id: projectId || undefined,
      })
      sessionRef.current = created.key
      if (seedMessages?.length) {
        writeCachedDetail(created.key, { key: created.key, title: '', messages: seedMessages, running: false } as unknown as ChatDetail)
      }
      if (navigateNow) navigate(`chat/${created.key}`, { replace: true })
      if (acp) await persistSelection('this agent', api.setSessionAcpAgent(created.key, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: selection.model && selection.model !== 'Auto' ? selection.model : undefined }))
      if (selection.approval !== 'normal') {
        const result = await persistSelection('this approval mode', api.setApprovalMode(selection.approval, created.key))
        if (result) {
          setSelection((sel) => ({ ...sel, approval: result.mode }))
          if (result.approval_screening.verdict === 'denied') notify(result.approval_screening.reason, 'warning')
        }
      }
      if (selection.taskMode !== 'agent') await persistSelection('this task mode', api.setTaskMode(selection.taskMode, created.key))
      if (selection.reasoning) await persistSelection('this reasoning effort', api.setReasoningEffort(created.key, selection.reasoning))
      if (naturalVoice.choice) await persistSelection('this natural-voice setting', api.setSessionNaturalVoice(created.key, naturalVoice.choice))
      return created.key
    })()
    ensureInFlightRef.current = p
    try {
      return await p
    } finally {
      ensureInFlightRef.current = null
    }
  }

  function handleSlashCommand(t: string): boolean {
    if (!t.startsWith('/')) return false
    const [cmd, ...rest] = t.split(/\s+/)
    const arg = rest.join(' ').trim()
    switch (cmd) {
      case '/clear':
        setInput(''); navigate('chat/new'); return true
      case '/prompts':
        if (arg) return false
        setInput(''); setPromptPaletteOpen(true); return true
      case '/model':
        setInput(''); setOpenModelSignal((n) => n + 1); return true
      case '/agent':
        setInput(''); setOpenAgentSignal((n) => n + 1); return true
      case '/effort':
        setInput(''); setOpenReasoningSignal((n) => n + 1); return true
      case '/tools':
        setInput(''); navigate('tools'); return true
      case '/project':
        setInput('')
        if (!started) setOpenProjectSignal((n) => n + 1)
        return true
      case '/help':
        setInput('')
        setTurns((prev) => [...prev, userTurn(t), assistantTurn(SLASH_HELP)])
        return true
      default:
        return false
    }
  }

  async function send(
    text = input,
    opts?: { original?: string; inputOrigin?: string; uiLabel?: string },
  ) {
    const t = text.trim()
    if (!t) return
    if (followups.length) setFollowups([])
    if (checkWorkOffer) setCheckWorkOffer(null)
    if (routingSuggestion) setRoutingSuggestion(null)
    const isStreaming = streamingRef.current
    if (!isStreaming && !opts?.original) {
      const m = t.match(/^\/optimize(?:\s+([\s\S]+))?$/i)
      if (m) {
        const prompt = (m[1] ?? '').trim()
        setInput('')
        if (prompt) void optimizeAndSend(prompt)
        return
      }
      const u = t.match(/^\/undo(?:\s+(\d+))?$/i)
      if (u) {
        setInput('')
        const n = Math.max(1, parseInt(u[1] ?? '1', 10) || 1)
        void undoTurns(n)
        return
      }
      const rw = t.match(/^\/rewind-to-turn\s+(\d+)(\s+--confirm)?$/i)
      if (rw) {
        setInput('')
        void rewindToTurn(parseInt(rw[1], 10), Boolean(rw[2]))
        return
      }
    }
    if (!isStreaming && handleSlashCommand(t)) return
    const original = opts?.original ?? (preOptimize !== null && preOptimize.trim() !== t ? preOptimize.trim() : undefined)
    if (isStreaming) {
      ensureSession()
        .then((s) => api.sendChat(t, s, undefined, 'steer'))
        .then((r) => {
          setInput((cur) => (cur === t ? '' : cur))
          if (r?.steered) setSteered((prev) => [...prev, t])
        })
        .catch(reportActionFailure('steer this turn'))
      return
    }
    const blocks = pasteBlocks
    const llmText = expandPasteMarkers(t, blocks)
    const files = [...mentionedFiles, ...attachedPaths]
    const turnPastes = pruneBlocks(t, blocks).map((b) => ({ seq: b.seq, lines: b.lines, content: b.content }))
    const clientTs = new Date().toISOString()
    const uiLabel = opts?.uiLabel?.trim() || undefined
    setTurns((prev) => [...prev, userTurn(uiLabel ?? original ?? t, clientTs, turnPastes.length ? turnPastes : undefined, files, original ? t : undefined)])
    if (!uiLabel) setPromptHistory((prev) => { const h = original ?? t; return (prev[prev.length - 1] === h ? prev : [...prev, h]).slice(-50) })
    const knowledgeIds = mentionedKnowledge.map((k) => k.id)
    const artifactSlugs = mentionedArtifacts.map((a) => a.slug)
    setInput(''); setPreOptimize(null); markStreaming(true); breakText.current = true
    setPasteBlocks([]); setMentionedFiles([]); setAttachedPaths([]); setMentionedKnowledge([]); setMentionedArtifacts([])
    let acceptedSession: string | null = null
    try {
      const meta: Record<string, unknown> = { client_ts: clientTs }
      if (files.length) meta.files = files
      if (knowledgeIds.length) meta.knowledge = knowledgeIds
      if (artifactSlugs.length) meta.artifacts = artifactSlugs
      if (turnPastes.length) meta.pastes = turnPastes
      if (original) meta.original = original
      if (uiLabel) meta.ui_label = uiLabel
      const seed: HistMsg[] = [{ role: 'user', content: llmText, ts: clientTs, meta: meta as HistMsg['meta'] }]
      const sid = await ensureSession(seed, false)
      acceptedSession = sid
      if (screenShare.sharing) await screenShare.captureAndStage(sid)
      await api.sendChat(llmText, sid, meta, undefined, opts?.inputOrigin)
      if (!sessionId) navigate(`chat/${sid}`, { replace: true })
    }
    catch (e) {
      const sid = acceptedSession
      const detail = sid ? await api.chatSessionDetail(sid).catch(() => null) : null
      const accepted = !!detail?.messages?.some((m) => m.role === 'user' && m.ts === clientTs)
      if (accepted && detail) {
        setTurns(hydrateTurns(detail.messages || [], detail.running))
        markStreaming(!!detail.running)
        if (sid && !sessionId) navigate(`chat/${sid}`, { replace: true })
      } else {
        markStreaming(false)
        setTurns((prev) => prev.filter((turn) => turn.ts !== clientTs))
        setInput((cur) => cur || t)
        setPasteBlocks(blocks)
        setMentionedFiles(mentionedFiles)
        setAttachedPaths(attachedPaths)
        setMentionedKnowledge(mentionedKnowledge)
        setMentionedArtifacts(mentionedArtifacts)
        setMicError(`Couldn’t send: ${(e as Error).message}`)
      }
    }
  }

  async function pinScreenFrame() {
    const sid = sessionRef.current
    const frame = screenShare.lastFrame()
    if (!sid) return
    if (!frame) {
      setMicError('Send a message while sharing first — there is no frame to pin yet.')
      window.setTimeout(() => setMicError(null), 6000)
      return
    }
    try {
      const r = await api.pinScreenFrame(sid, frame)
      if (r?.path) setAttachedPaths((prev) => [...prev, r.path])
    } catch (e) {
      setMicError((e as Error)?.message || 'Could not pin the frame.')
      window.setTimeout(() => setMicError(null), 6000)
    }
  }

  async function activatePlanMode() {
    const sid = sessionRef.current
    if (!sid) return
    try {
      const r = await api.chatPlanActivate(sid)
      setSelection((sel) => ({ ...sel, taskMode: 'plan' }))
      if (r.parked) {
        setMicError('This run is parked — approve the plan below to resume it.')
        window.setTimeout(() => setMicError(null), 6000)
      }
    } catch (e) {
      setMicError((e as Error)?.message || 'Could not start plan mode.')
      window.setTimeout(() => setMicError(null), 6000)
    }
  }

  async function optimize() {
    const t = input.trim()
    if (!t || optimizing) return
    setOptimizing(true)
    try {
      const ctx = buildOptimizerContext(turns)
      const r = await api.optimizePrompt(t, ctx)
      if (r.changed && r.optimized) { setPreOptimize(input); setInput(r.optimized) }
      else notify('This prompt is already clear — no changes needed.', 'info')
    } catch (e) {
      const detail = String((e as Error)?.message || e)
      notify(isNoModelSetupError(detail) ? 'Connect a model before optimizing this prompt.' : `Couldn't optimize this prompt: ${detail}`, 'error')
    }
    finally { setOptimizing(false) }
  }
  async function optimizeAndSend(raw: string) {
    setOptimizing(true)
    let optimized = ''
    try {
      const ctx = buildOptimizerContext(turns)
      const r = await api.optimizePrompt(raw, ctx)
      if (r.changed && r.optimized && r.optimized.trim() !== raw) optimized = r.optimized.trim()
    } catch {   }
    finally { setOptimizing(false) }
    if (optimized) await send(optimized, { original: raw })
    else await send(raw)
  }
  function revertOptimize() {
    if (preOptimize === null) return
    setInput(preOptimize)
    setPreOptimize(null)
    requestAnimationFrame(() => composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus())
  }
  async function undoTurns(n: number) {
    const s = sessionRef.current
    if (!s) return
    try {
      const r = await api.undoChat(s, n)
      const d = await api.chatSessionDetail(s)
      const rehydrated = hydrateTurns(d.messages || [], false)
      setTurns([...rehydrated, assistantTurn(r.notice)])
    } catch {   }
  }
  async function rewindToTurn(turn: number, confirm: boolean) {
    const s = sessionRef.current
    if (!s) return
    const fmt = (f: RewindFileWire) =>
      f.action === 'not_captured'
        ? `- \`${f.path}\` — NOT captured (${f.reason}); it will not be restored`
        : f.action === 'delete'
          ? `- \`${f.path}\` — would be DELETED (it did not exist at turn ${turn})`
          : f.action === 'unchanged'
            ? `- \`${f.path}\` — already matches turn ${turn}; no change`
            : `- \`${f.path}\` — restore ${f.current_size} → ${f.restored_size} bytes`
    try {
      if (!confirm) {
        const p = await api.rewindPreview(s, turn)
        const lines = [
          `**Rewind to turn ${turn} — preview.** Nothing has been written yet.`,
          ...(p.warnings || []).map((w) => `> ${w}`),
          ...(p.files || []).map(fmt),
          (p.files || []).length === 0 ? '_No recorded file changes after that turn._' : '',
          `Run \`/rewind-to-turn ${turn} --confirm\` to apply. This restores files only — the conversation stays as the record of what happened.`,
        ].filter(Boolean)
        setTurns((prev) => [...prev, assistantTurn(lines.join('\n'))])
        return
      }
      const r = await api.rewindToTurn(s, turn)
      const lines = [
        r.notice,
        ...r.restored.map((p) => `- restored \`${p}\``),
        ...r.deleted.map((p) => `- deleted \`${p}\``),
        ...r.errors.map((e) => `- FAILED: ${e}`),
      ]
      setTurns((prev) => [...prev, assistantTurn(lines.join('\n'))])
    } catch (e) {
      reportActionFailure(`rewind to turn ${turn}`)(e)
    }
  }
  async function transcribe(blob: Blob, opts?: { duplex?: boolean }): Promise<string> {
    const r = await api.transcribeAudio(blob, { duplex: opts?.duplex, session: sessionRef.current || '' })
    if (r.error) {
      const msg = /not available/i.test(r.error)
        ? 'Voice input needs a speech-to-text model — configure one in Settings → AI & Models.'
        : `Couldn’t transcribe audio: ${r.error}`
      setMicError(msg)
      window.setTimeout(() => setMicError(null), 6000)
      return ''
    }
    if (r.filtered === 'echo') {
      setMicError('Ignored the assistant’s own voice coming back through the microphone.')
      window.setTimeout(() => setMicError(null), 4000)
      return ''
    }
    setMicError(null)
    return r.text ?? ''
  }

  function onMentionFile(file: { path: string; name: string }) {
    setMentionedFiles((prev) => (prev.includes(file.path) ? prev : [...prev, file.path]))
  }
  function onMentionKnowledge(item: { id: string; name: string }) {
    setMentionedKnowledge((prev) => (prev.some((k) => k.id === item.id) ? prev : [...prev, item]))
  }
  function onLargePaste(text: string): boolean {
    if (!shouldCollapsePaste(text)) return false
    const seq = ++pasteSeq.current
    const block: PasteBlock = { id: makePasteId(seq), seq, lines: text.split('\n').length, content: text }
    const marker = markerFor(seq)
    setPasteBlocks((prev) => [...prev, block])
    setInput((prev) => prev + marker)
    requestAnimationFrame(() => composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus())
    return true
  }
  function removePaste(seq: number) {
    setPasteBlocks((prev) => prev.filter((b) => b.seq !== seq))
    setInput((prev) => prev.replace(markerFor(seq), '').replace(/  +/g, ' '))
  }

  async function stop() {
    markStreaming(false)
    if (sessionRef.current) await api.stopChat(sessionRef.current).catch(reportActionFailure('stop this turn'))
  }

  const [editingTurn, setEditingTurn] = useState<number | null>(null)

  async function regenerate() {
    const s = sessionRef.current
    if (!s || streaming) return
    setTurns((prev) => {
      const i = prev.map((t) => t.role).lastIndexOf('assistant')
      return i >= 0 ? prev.slice(0, i) : prev
    })
    markStreaming(true); breakText.current = true
    try { await api.regenerate(s) }
    catch (e) { markStreaming(false); patchLastAssistant((segs) => [...segs, { kind: 'text', text: `⚠️ ${(e as Error).message}` }]) }
  }

  async function switchVariant(index: number) {
    const s = sessionRef.current
    if (!s || streaming) return
    try { await api.switchVariant(s, index) }
    catch (e) {
      setMicError(`Couldn’t switch answer: ${(e as Error).message}`)
      window.setTimeout(() => setMicError(null), 6000)
    }
  }

  function forkAt(turnIndex: number) {
    const s = sessionRef.current
    if (!s) return
    api.forkSession(s, branchIndexOf(turns, turnIndex))
      .then((r) => {
        if (!r?.key) return
        notify('Session branched — you’re now in the new branch.', 'success')
        navigate(`chat/${r.key}`)
      })
      .catch((e: Error) => {
        setMicError(`Couldn’t branch this chat: ${e.message}`)
        window.setTimeout(() => setMicError(null), 6000)
      })
  }

  async function editResend(turnIndex: number, content: string, rewind = false) {
    const s = sessionRef.current
    const t = content.trim()
    if (!s || !t || streaming) return
    const turn = turns[turnIndex]
    const previousTurns = turns
    setEditingTurn(null)
    const newTs = new Date().toISOString()
    setTurns((prev) => [...prev.slice(0, turnIndex), userTurn(t, newTs)])
    markStreaming(true); breakText.current = true
    try { await api.editResend(s, t, turn?.ts, turnIndex, newTs, rewind) }
    catch (e) {
      const detail = await api.chatSessionDetail(s).catch(() => null)
      const accepted = !!detail?.messages?.some((m) => m.role === 'user' && m.ts === newTs)
      if (accepted && detail) {
        setTurns(hydrateTurns(detail.messages || [], detail.running))
        markStreaming(!!detail.running)
      } else {
        markStreaming(false)
        setTurns(previousTurns)
        setInput(t)
        setMicError(`Couldn’t resend: ${(e as Error).message}`)
      }
    }
  }

  async function rewindTo(turnIndex: number) {
    const turn = turns[turnIndex]
    if (!turn || turn.role !== 'user') return
    const later = turns.length - 1 - turnIndex
    if (!(await confirm({
      title: 'Rewind to this message?',
      body: `The ${later} later message${later === 1 ? '' : 's'} will be replayed from here — the current answers are kept in this chat's history and can be forked back.`,
      confirmLabel: 'Rewind',
    }))) return
    await editResend(turnIndex, turnText(turn), true)
  }

  async function forkRewound(turnIndex: number, snapshotIndex?: number) {
    const s = sessionRef.current
    if (!s) return
    try {
      const r = await api.forkRewound(s, turnIndex, snapshotIndex)
      if (r?.key) navigate(`chat/${r.key}`)
    } catch (e) {
      setMicError(`Couldn’t restore that history: ${(e as Error).message}`)
      window.setTimeout(() => setMicError(null), 6000)
    }
  }

  const audioCtxRef = useRef<AudioContext | null>(null)
  const audioPlayHeadRef = useRef(0)
  const audioSourcesRef = useRef<AudioBufferSourceNode[]>([])

  const { data: voiceCfgRaw } = useQuery('chat:voice-config', () => api.gideonConfig().then((c) => c.voice as VoiceLoopConfig), { persist: true })
  const voiceCfg: VoiceLoopConfig = {
    confirmation_phrases: voiceCfgRaw?.confirmation_phrases?.length ? voiceCfgRaw.confirmation_phrases : DEFAULT_CONFIRMATION_PHRASES,
    exit_phrases: voiceCfgRaw?.exit_phrases?.length ? voiceCfgRaw.exit_phrases : DEFAULT_EXIT_PHRASES,
    duplex_mute_enabled: voiceCfgRaw?.duplex_mute_enabled ?? true,
  }
  const [speakingTurn, setSpeakingTurn] = useState<number | null>(null)
  const speakGenRef = useRef(0)

  function getAudioCtx(): AudioContext | null {
    if (!audioCtxRef.current) {
      const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Ctor) return null
      audioCtxRef.current = new Ctor()
    }
    return audioCtxRef.current
  }

  function stopSpeak() {
    speakGenRef.current++
    for (const src of audioSourcesRef.current) { try { src.stop() } catch {   } }
    audioSourcesRef.current = []
    audioPlayHeadRef.current = 0
    setSpeakingTurn(null)
  }

  function speak(text: string, turnIndex: number) {
    if (speakingTurn === turnIndex) { stopSpeak(); return }
    stopSpeak()
    const s = sessionRef.current
    const ctx = getAudioCtx()
    if (ctx && ctx.state === 'suspended') ctx.resume().catch(() => {})
    speakGenRef.current++
    setSpeakingTurn(turnIndex)
    return api.voiceSynthesize(text, s ?? '').catch((e: Error) => {
      setSpeakingTurn((cur) => (cur === turnIndex ? null : cur))
      const msg = /TTS voice|no.*voice|Settings/i.test(e.message)
        ? 'Text-to-speech needs a voice — choose one in Settings → AI & Models.'
        : `Couldn’t play audio: ${e.message}`
      setMicError(msg)
      window.setTimeout(() => setMicError(null), 6000)
    })
  }
  async function enqueueAudio(b64: string) {
    const ctx = getAudioCtx()
    if (!ctx) return
    if (ctx.state === 'suspended') await ctx.resume().catch(() => {})
    const gen = speakGenRef.current
    let bytes: Uint8Array
    try {
      const bin = atob(b64)
      bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
    } catch { return }
    let buf: AudioBuffer
    try { buf = await ctx.decodeAudioData(bytes.buffer.slice(0) as ArrayBuffer) }
    catch { return }
    if (gen !== speakGenRef.current) return
    const now = ctx.currentTime
    const startAt = Math.max(now, audioPlayHeadRef.current)
    const src = ctx.createBufferSource()
    src.buffer = buf
    src.connect(ctx.destination)
    src.start(startAt)
    audioSourcesRef.current.push(src)
    audioPlayHeadRef.current = startAt + buf.duration
    src.onended = () => {
      audioSourcesRef.current = audioSourcesRef.current.filter((x) => x !== src)
      if (gen === speakGenRef.current && audioSourcesRef.current.length === 0) setSpeakingTurn(null)
    }
  }

  function insertPrompt(text: string) {
    if (!text) return
    setInput((prev) => (prev ? `${prev}\n${text}` : text))
    requestAnimationFrame(() => composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus())
  }

  function quoteToComposer(text: string, attribution?: string) {
    const q = text.trim()
    if (!q) return
    const lines = q.split('\n').map((l) => `> ${l}`)
    const block = attribution ? `> **${attribution} said:**\n${lines.join('\n')}` : lines.join('\n')
    setInput((prev) => (prev ? `${prev}\n\n${block}\n\n` : `${block}\n\n`))
    composerRef.current?.querySelector<HTMLElement>('.cm-content')?.focus()
  }

  function attributionForNode(node: Node | null): string | undefined {
    if (!node) return undefined
    for (const [idx, el] of turnNodes.current.entries()) {
      if (el.contains(node)) {
        const turn = turns[idx]
        if (!turn) return undefined
        return turn.role === 'user' ? 'You' : (selection.agent || 'Assistant')
      }
    }
    return undefined
  }

  const activity = useMemo(() => deriveActivity(turns), [turns])
  const nodeForTurn = useCallback((turnIndex: number) => turnNodes.current.get(turnIndex), [])
  const jumpToTurn = useMemo(() => createScrollToTurnHandler(nodeForTurn), [nodeForTurn])

  async function killFanout() {
    const s = sessionRef.current
    if (!s) return
    setSubagents((prev) => prev.map((c) => (c.done ? c : { ...c, done: true, error: 'cancelled' })))
    await api.cancelFanout(s).catch(reportActionFailure('cancel the subagents'))
  }

  async function openSide() {
    const s = sessionRef.current
    if (!s || sideOpenedRef.current) return
    sideOpenedRef.current = true
    await api.sideOpen(s).catch(() => {})
  }
  async function askSide(question: string) {
    const s = sessionRef.current
    const q = question.trim()
    if (!s || !q || sideBusy) return
    await openSide()
    setSideBusy(true)
    setSideMsgs((prev) => [...prev, { q, a: '', runId: '', done: false }])
    try {
      const res = await api.sideTurn(s, q)
      setSideMsgs((prev) => { const i = prev.length - 1; if (i < 0) return prev; const n = [...prev]; if (!n[i].runId) n[i] = { ...n[i], runId: res.run_id }; return n })
    } catch (e) {
      setSideBusy(false)
      setSideMsgs((prev) => { const i = prev.length - 1; if (i < 0) return prev; const n = [...prev]; n[i] = { ...n[i], a: `⚠️ Couldn’t answer: ${(e as Error).message}`, done: true }; return n })
    }
  }

  function acpFor(agentName: string): { providerId: string; agent: DiscoveredAgent } | null {
    for (const [providerId, list] of Object.entries(data.discovered ?? {})) {
      const agent = list.find((dd) => dd.name === agentName)
      if (agent) return { providerId, agent }
    }
    return null
  }

  const persistSelection = <T,>(what: string, p: Promise<T>): Promise<T | void> =>
    p.catch((e) => { notify(`Couldn't apply ${what} to this session: ${String((e as Error)?.message || e)}`, 'error') })

  async function selectNaturalVoice(choice: '' | 'on' | 'off') {
    const s = sessionRef.current
    if (!s) { setNaturalVoice((v) => ({ ...v, choice, source: '' })); return }
    const r = await persistSelection('this natural-voice setting', api.setSessionNaturalVoice(s, choice))
    if (r) setNaturalVoice({
      choice: (r.natural_voice || '') as '' | 'on' | 'off',
      effective: !!r.natural_voice_effective,
      source: r.natural_voice_source || '',
      agentDefault: !!r.natural_voice_agent_default,
    })
  }

  function applySelection(patch: Partial<ComposerValue>) {
    const nextSel = { ...selection, ...patch }
    setSelection(nextSel)
    const s = sessionRef.current
    if (!s) return
    const acp = acpFor(nextSel.agent)
    if (patch.agent) {
      if (acp) persistSelection('this agent', api.setSessionAcpAgent(s, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: nextSel.model && nextSel.model !== 'Auto' ? nextSel.model : undefined }))
      else persistSelection('this agent', api.setSessionAgent(s, patch.agent))
    }
    if (patch.model && !patch.agent) {
      if (acp) persistSelection('this model', api.setSessionAcpAgent(s, { provider: acp.providerId, provider_agent: acp.agent.provider_agent, model: patch.model === 'Auto' ? undefined : patch.model }))
      else persistSelection('this model', api.setSessionModel(s, patch.model === 'Auto' ? '' : patch.model))
    }
    if (patch.approval) {
      api.setApprovalMode(patch.approval as ApprovalMode, s).then((result) => {
        setSelection((sel) => ({ ...sel, approval: result.mode }))
        if (result.approval_screening.verdict === 'denied') notify(result.approval_screening.reason, 'warning')
      }).catch(reportActionFailure('apply this approval mode'))
    }
    if (patch.taskMode) persistSelection('this task mode', api.setTaskMode(patch.taskMode as TaskMode, s))
    if (patch.reasoning !== undefined) persistSelection('this reasoning effort', api.setReasoningEffort(s, patch.reasoning as ReasoningEffort))
  }

  function applyTemplate(prefill: StarterPrefill) {
    if (Object.keys(prefill.selection).length)
      applySelection({ ...prefill.selection, reasoning: prefill.selection.reasoning as ReasoningEffort | undefined })
    if (prefill.input) setInput(prefill.input)
    notify(`Started from "${prefill.name}".`, 'info')
  }

  async function saveAsTemplate() {
    const name = await promptInput({
      title: 'Save as starter',
      body: 'Saves this chat\'s agent, model and reasoning effort — not its messages.',
      label: 'Starter name',
      placeholder: 'e.g. Research deep dive',
      confirmLabel: 'Save',
    })
    if (!name) return
    try {
      await api.createSessionTemplate({
        name,
        agent: selection.agent || '',
        model: selection.model && selection.model !== 'Auto' ? selection.model : '',
        reasoning_effort: selection.reasoning || '',
        first_prompt: '',
      })
      invalidateKeys('chat:starters')
      notify(`Saved "${name}" — it'll appear on the new-chat screen.`, 'success')
    } catch (e) {
      notify(`Couldn't save this starter: ${String((e as Error)?.message || e)}`, 'error')
    }
  }

  async function switchToAgentAndRun(continuation: string) {
    setSelection((sel) => ({ ...sel, taskMode: 'agent' }))
    const s = sessionRef.current
    if (s) {
      try { await api.setTaskMode('agent', s) }
      catch (e) {
        notify(`Couldn't switch this session to Agent: ${String((e as Error)?.message || e)}`, 'error')
        return
      }
    }
    const text = continuation.trim() || 'Go ahead and do it.'
    await send(text)
  }

  function beginRename() { setRenameVal(title || ''); setRenaming(true) }
  async function commitRename() {
    const s = sessionRef.current
    const v = renameVal.trim()
    setRenaming(false)
    if (!s || !v || v === title) return
    setTitle(v)
    await api.renameSession(s, v).catch(reportActionFailure('rename this chat'))
    invalidateKeys('chat:sessions', true)
  }
  async function regenTitle() {
    const s = sessionRef.current
    if (!s || regenningTitle) return
    setRegenningTitle(true)
    try {
      const r = await api.generateTitle(s).catch(() => null)
      if (r?.title) setTitle(r.title)
    } finally {
      setRegenningTitle(false)
    }
  }
  async function copyLink() {
    const s = sessionRef.current
    if (!s) return
    const url = `${location.origin}${location.pathname}#/chat/${encodeURIComponent(s)}`
    if (!(await copyText(url, 'the chat link'))) return
    setLinkCopied(true)
    window.setTimeout(() => setLinkCopied(false), 1600)
  }
  async function briefAgent() {
    const s = sessionRef.current
    if (!s) return
    const content = await promptInput({
      title: 'Brief the agent', type: 'textarea',
      label: 'Background context to prime the next reply (not shown in the transcript)',
      placeholder: 'Paste a spec, reference, or correction…', confirmLabel: 'Add context',
    })
    if (!content?.trim()) return
    try { await api.briefSession(s, content.trim()); setToast('Context added — it primes your next message.') }
    catch (e) { setToast((e as Error).message || 'Failed to add context') }
    window.setTimeout(() => setToast(null), 2600)
  }
  async function setWorkspaceDir() {
    const s = sessionRef.current
    if (!s) return
    const dir = await promptInput({
      title: 'Working directory', label: 'Absolute path for the agent’s working directory',
      placeholder: '/Users/you/project', confirmLabel: 'Set',
    })
    if (dir == null) return
    try { await api.setSessionWorkspaceDir(s, dir.trim()); setToast(dir.trim() ? `Working directory set to ${dir.trim()}` : 'Working directory cleared') }
    catch (e) { setToast((e as Error).message || 'Failed to set working directory') }
    window.setTimeout(() => setToast(null), 2600)
  }

  async function attach(files: File[]) {
    setAttachError(null)
    const { precheck } = await import('../shared/data/chunkedUpload')
    const ok: File[] = []
    const rejected: string[] = []
    for (const f of files) {
      const err = await precheck(f)
      if (err) rejected.push(err)
      else ok.push(f)
    }
    if (rejected.length) setAttachError(rejected.join(' · '))
    if (!ok.length) return
    const ctrl = new AbortController()
    uploadAbortRef.current = ctrl
    setUploads(ok.map((f) => ({ name: f.name, pct: 0 })))
    const r = await api.uploadFiles(ok, (idx, p) => {
      setUploads((prev) => prev.map((u, i) => (i === idx ? { ...u, pct: p.pct } : u)))
    }, ctrl.signal).catch(async (e) => {
      const { isAbortError } = await import('../shared/data/chunkedUpload')
      if (!isAbortError(e)) setAttachError((e as Error).message)
      return { paths: [] as string[] }
    })
    uploadAbortRef.current = null
    setUploads([])
    const paths = (r as { paths?: string[] }).paths ?? []
    if (paths.length) setAttachedPaths((prev) => [...prev, ...paths.filter((p) => !prev.includes(p))])
  }

  async function captureNative(): Promise<string> {
    try {
      const r = await api.screenshot()
      if (r.error) return r.error
      if (r.path) setAttachedPaths((prev) => (prev.includes(r.path) ? prev : [...prev, r.path]))
      return ''
    } catch (e) { return (e as Error).message || 'Screen capture failed' }
  }

  async function captureInBrowser() {
    const r = await grabOneFrame()
    if ('error' in r) {
      if (r.error === 'cancelled') return
      setAttachError(r.error === 'unsupported'
        ? 'This browser cannot capture the screen.'
        : 'The screen capture did not produce a frame.')
      return
    }
    setSnip({ url: r.frame.toDataURL('image/png'), width: r.frame.width, height: r.frame.height, source: r.frame })
  }

  async function captureScreenArea() {
    setAttachError(null)
    if (captureProvider === 'native') {
      const err = await captureNative()
      if (!err) return
      if (chooseCaptureProvider(platform, displayCapture, true) !== 'browser') { setAttachError(err); return }
    }
    await captureInBrowser()
  }

  async function attachSnip(rect: SnipRect) {
    const src = snip
    setSnip(null)
    if (!src) return
    const file = await cropToPngFile(src.source, rect)
    if (!file) { setAttachError('The cropped capture could not be encoded.'); return }
    await attach([file])
  }

  const stage = (
    <div data-tour="chat" className="w-full" style={{ maxWidth: 'var(--content-width)' }}>
      {
}
      {memoryMode !== 'persistent' && (
        <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-on-surface-low">
          {memoryMode === 'incognito' ? <EyeOff size={13} className="shrink-0" /> : <Clock size={13} className="shrink-0" />}
          <span>{memoryMode === 'incognito'
            ? 'Incognito — memory writes are disabled. This chat is still saved to your history.'
            : 'Temporary — this chat is forgotten when the session ends.'}</span>
        </div>
      )}
      {
}
      {micError && (
        <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-danger">
          <AlertTriangle size={13} className="shrink-0" /><span>{micError}</span>
        </div>
      )}
      {toast && (
        <div className="mb-2 flex items-center gap-1.5 text-[0.75rem] text-on-surface-var">
          <Check size={13} className="shrink-0 text-ok" /><span>{toast}</span>
        </div>
      )}
      {
}
      {attachError && (
        <div role="alert" className="mb-2 flex items-start gap-1.5 rounded-md px-2.5 py-1.5 text-[0.75rem]"
          style={{ background: 'color-mix(in srgb, var(--color-danger) 12%, transparent)', color: 'var(--color-danger)' }}>
          <AlertTriangle size={13} className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1 break-words">{attachError}</span>
          <IconButton icon={X} label="Dismiss" onClick={() => setAttachError(null)} size={20} iconSize={12}
            className="shrink-0 opacity-70 hover:opacity-100" />
        </div>
      )}
      {
}
      {uploads.length > 0 && (
        <div className="mb-2 flex flex-col gap-1 rounded-lg bg-surface-container/60 px-3 py-2">
          {uploads.map((u) => (
            <div key={u.name} className="flex items-center gap-2.5 text-[0.75rem] text-on-surface-var">
              <Loader2 size={13} className="shrink-0 animate-spin text-primary" />
              <span className="max-w-[40%] shrink-0 truncate" title={u.name}>{u.name}</span>
              {
}
              <Meter size="thin" className="min-w-0 flex-1" label={`Uploading ${u.name}`} pct={u.pct} />
              <span className="shrink-0 tabular-nums text-on-surface-low">{u.pct}%</span>
              <IconButton icon={X} label="Cancel upload" onClick={() => uploadAbortRef.current?.abort()} size={20} iconSize={13}
                tone="danger" className="shrink-0" />
            </div>
          ))}
        </div>
      )}
      <MentionChips paths={[...mentionedFiles, ...attachedPaths]}
        onRemove={(p) => { setMentionedFiles((prev) => prev.filter((x) => x !== p)); setAttachedPaths((prev) => prev.filter((x) => x !== p)) }}
        onOpen={setOpenFile} />
      <KnowledgeChips items={mentionedKnowledge}
        onRemove={(id) => setMentionedKnowledge((prev) => prev.filter((k) => k.id !== id))} />
      <PasteCards blocks={pasteBlocks} onRemove={removePaste} />
      {
}
      {preOptimize !== null && (
        <Button variant="secondary" size="xs" onClick={revertOptimize}
          className="mb-2 gap-1.5 px-2.5 text-[0.75rem] text-on-surface-var">
          <Repeat size={12} className="shrink-0" /> Optimized — revert to original
        </Button>
      )}
      {
}
      {steered.length > 0 && (
        <div className="mb-2 flex flex-col gap-1" aria-live="polite">
          {steered.map((s, i) => (
            <div key={`${i}-${s.slice(0, 24)}`}
              className="flex items-start gap-1.5 text-[0.75rem] text-on-surface-var">
              <CornerDownLeft size={12} className="mt-0.5 shrink-0" aria-hidden />
              <span className="min-w-0 flex-1 truncate">
                Steered into this answer: {s}
              </span>
            </div>
          ))}
        </div>
      )}
      {
}
      <QueueStack items={queued} canInterrupt={streaming}
        onCancel={(id) => { setQueued((prev) => prev.filter((q) => q.id !== id)); const s = sessionRef.current; if (s) api.cancelQueued(s, id).catch(reportActionFailure('cancel that queued message')) }}
        onEdit={(id, content) => {
          setQueued((prev) => prev.filter((q) => q.id !== id)); const s = sessionRef.current; if (s) api.cancelQueued(s, id).catch(reportActionFailure('cancel that queued message'))
          setInput((cur) => (cur.trim() ? cur : content))
        }}
        onInterrupt={(id) => {
          const s = sessionRef.current; if (s) api.interruptChat(s, id).catch(reportActionFailure('interrupt this turn'))
        }} />
      <div className="relative">
        {
}
        {promptPaletteOpen && (
          <PromptPalette
            onInsert={insertPrompt}
            onSend={(t) => { const full = input.trim() ? `${input}\n${t}` : t; setInput(''); void send(full) }}
            onClose={() => setPromptPaletteOpen(false)} />
        )}
        {artifactPickerOpen && (
          <ArtifactContextPicker
            attached={mentionedArtifacts}
            onPick={(a) => setMentionedArtifacts((prev) => (prev.some((x) => x.slug === a.slug) ? prev : [...prev, a]))}
            onRemove={(slug) => setMentionedArtifacts((prev) => prev.filter((a) => a.slug !== slug))}
            onClose={() => setArtifactPickerOpen(false)} />
        )}
        {
}
        {snip && (
          <SnipOverlay frame={snip.url} width={snip.width} height={snip.height}
            onCancel={() => setSnip(null)} onConfirm={(rect) => { void attachSnip(rect) }} />
        )}
        {knowledgePickerOpen && (
          <KnowledgeContextPicker
            attached={mentionedKnowledge}
            onPick={(item) => onMentionKnowledge(item)}
            onRemove={(id) => setMentionedKnowledge((prev) => prev.filter((k) => k.id !== id))}
            onClose={() => setKnowledgePickerOpen(false)} />
        )}
        {started && sessionRef.current && (
          <div className="mb-1 flex justify-center">
            <SessionSkillsReview sessionKey={sessionRef.current} agent={selection.agent || undefined} refreshKey={sessionSkillsEpoch} />
          </div>
        )}
        <AnimatePresence>
          {routingSuggestion && (
            <div className="mb-1 flex justify-center">
              <RoutingChip suggestion={routingSuggestion} defaultAgent={selection.agent || ''}
                onRoute={() => { setSelection((s) => ({ ...s, agent: routingSuggestion.agent })); setRoutingSuggestion(null) }}
                onDismiss={() => setRoutingSuggestion(null)} />
            </div>
          )}
        </AnimatePresence>
        {
}
        {started && sessionRef.current && (
          <div className="mb-1 flex justify-center">
            <OrganizeChip sessionKey={sessionRef.current} refreshKey={turns.length} />
          </div>
        )}
        {
}
        {sessionRef.current && (
          <ChatPlanGate session={sessionRef.current} refreshKey={turns.length}
            onTaskMode={(m) => setSelection((sel) => ({ ...sel, taskMode: m }))} />
        )}
        <ComposerStage ref={composerRef} value={input} onChange={(v) => { setInput(v); if (preOptimize !== null) setPreOptimize(null); if (followups.length && v.trim().length >= 3) setFollowups([]) }} onSend={() => send()}
          streaming={streaming} onStop={stop} controls={CHAT_CONTROLS} data={data}
          selection={selection} onSelect={applySelection} onAttach={attach} onFocusChange={setComposerFocused}
          naturalVoice={{ ...naturalVoice, onSelect: (c) => void selectNaturalVoice(c) }}
          onOpenPrompts={() => setPromptPaletteOpen(true)}
          plusMenuExtra={(close) => (
            <>
              <MenuRow icon={<BookText size={16} />} label="Add knowledge" hint="Search the library → attach to the prompt" onClick={() => { close(); setKnowledgePickerOpen(true) }} />
              <MenuRow icon={<Boxes size={16} />} label="Reference an artifact" hint="Ground the reply in an artifact's current version" onClick={() => { close(); setArtifactPickerOpen(true) }} />
              {
}
              {captureProvider !== 'none' && <MenuRow icon={<Camera size={16} />} label="Capture screen area" hint="Snip a region → attach" onClick={() => { close(); void captureScreenArea() }} />}
              {
}
              {screenShare.sharing && sessionRef.current && (
                <MenuRow icon={<Pin size={16} />} label="Pin shared frame" hint="Save the current screen frame as an ordinary attachment"
                  onClick={() => { close(); void pinScreenFrame() }} />
              )}
              {
}
              {started && sessionRef.current && (
                <MenuRow icon={<ListChecks size={16} />} label="Plan this first"
                  hint="Draft a plan for review — nothing runs until you approve it"
                  onClick={() => { close(); void activatePlanMode() }} />
              )}
              {started && sessionRef.current && <AutoNudgeMenuItem session={sessionRef.current!} onOpen={close} />}
            </>
          )}
          onMentionFile={onMentionFile} onMentionKnowledge={onMentionKnowledge} onLargePaste={onLargePaste}
          openModelSignal={openModelSignal} openAgentSignal={openAgentSignal} openReasoningSignal={openReasoningSignal}
          onOptimize={optimize} optimizing={optimizing} history={promptHistory}
          onTranscribe={transcribe} onMicError={(m) => { setMicError(m); window.setTimeout(() => setMicError(null), 6000) }} canQueue contextPct={contextPct}
          handsFree={{ confirmationPhrases: voiceCfg.confirmation_phrases, exitPhrases: voiceCfg.exit_phrases, speaking: speakingTurn !== null, muteWhileSpeaking: voiceCfg.duplex_mute_enabled }}
          onHandsFreeSubmit={(t) => void send(t, { inputOrigin: 'voice' })}
          screenShare={{ available: screenShare.available, sharing: screenShare.sharing, disabledReason: screenShare.disabledReason, onToggle: screenShare.toggle }} />
      </div>
      {
}
      {!sessionId && (
        <div className="mt-2.5 flex flex-wrap items-center justify-center gap-x-3 gap-y-2">
          <ProjectPicker value={projectId} onChange={setProjectId}
            emptyLabel="No project" emptyHint="" openSignal={openProjectSignal} />
          <Segmented ariaLabel="Memory mode" size="sm" value={memoryMode}
            options={MEMORY_MODES.map((m) => ({ key: m.id, label: m.label, title: `${m.label} — ${m.hint}` }))}
            onChange={(v) => setMemoryMode(v as MemoryMode)} />
        </div>
      )}
    </div>
  )

  return (
    <div className="relative flex h-full flex-col overflow-hidden">
      <DotGlow intensity={composerFocused ? 1.6 : 1} composerRef={composerRef} focusRef={glowTargetRef} />

      {
}
      <TopBar
        keepCornerPadding
        left={!started ? (
          undefined
        ) : (
          renaming ? (
            <input autoFocus aria-label="Rename this chat" value={renameVal} onChange={(e) => setRenameVal(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); else if (e.key === 'Escape') setRenaming(false) }}
              className="h-8 min-w-[200px] max-w-[420px] rounded-md bg-surface-high px-2 text-on-surface text-[0.9375rem] outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />
          ) : (
            <div className="flex items-center gap-1.5 min-w-0">
              {
}
              <IconButton icon={ArrowLeft} label="Back to chat history" size={40} onClick={() => navigate('chat/history')} />
              <button type="button" onClick={beginRename} title="Rename chat"
                className="group inline-flex items-center gap-1.5 min-w-0 max-w-[420px] text-on-surface hover:text-on-surface-var transition-colors">
                <span data-type="title-l" className="truncate">{sessionTitle({ key: sessionRef.current ?? '', title })}</span>
                <Pencil size={13} className="shrink-0 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity" />
              </button>
              {
}
              {sessionRef.current && (
                <IconButton icon={Sparkles} label="Regenerate title" onClick={regenTitle}
                  loading={regenningTitle} disabled={regenningTitle} size={20} iconSize={12}
                  className="shrink-0 -ml-0.5 self-start text-on-surface-low hover:text-primary" />
              )}
              {
}
              {screenShare.sharing && <ScreenShareChip onStop={screenShare.toggle} />}
              {
}
              {projectName && (
                <button type="button" onClick={() => navigate(`projects/${projectId}`)}
                  className="inline-flex shrink-0 items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-[0.75rem] text-on-surface-var hover:text-on-surface" title={`Scoped to project: ${projectName}`}>
                  <FolderKanban size={12} className="text-primary" /> {projectName}
                </button>
              )}
              {
}
              {sessionCost && (
                <span
                  className="inline-flex shrink-0 items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-[0.75rem] text-on-surface-var"
                  title={sessionCost.priced ? 'What this conversation has cost so far' : 'Cost so far — includes a model with no price row, so this is a partial total'}>
                  <Coins size={12} className="text-primary" />
                  {sessionCost.priced ? `$${sessionCost.cost.toFixed(sessionCost.cost < 1 ? 4 : 2)}` : 'unpriced'}
                  {' · '}{fmtTokens(sessionCost.tokens)} tokens
                </span>
              )}
              {
}
              {branchedFrom && (
                branchedFrom.title ? (
                  <Button size="xs" variant="secondary"
                    onClick={() => navigate(`chat/${branchedFrom.key}`)}
                    title={`Branched from "${branchedFrom.title}" — open the original`}>
                    <GitBranch size={12} className="text-primary" /> Branched from {branchedFrom.title}
                  </Button>
                ) : (
                  <span className="inline-flex shrink-0 items-center gap-1 rounded-pill bg-surface-high px-2 py-0.5 text-[0.75rem] text-on-surface-var"
                    title="This chat was branched from a conversation that no longer exists">
                    <GitBranch size={12} className="text-on-surface-low" /> Branched from a deleted chat
                  </span>
                )
              )}
              {
}
              {investigateOrigin?.title && (
                <Button size="xs" variant="secondary"
                  onClick={() => { if (investigateOrigin.back_link) navigate(investigateOrigin.back_link.replace(/^#\//, '')) }}
                  title={`Investigating: ${investigateOrigin.title} — open the source`}>
                  <MessageCircleQuestion size={12} className="text-primary" /> {investigateOrigin.title}
                </Button>
              )}
              { }
              {sessionRef.current && (
                <IconButton icon={linkCopied ? Check : Link2} label={linkCopied ? 'Link copied' : 'Copy chat link'} size={40} onClick={copyLink} />
              )}
            </div>
          )
        )}
        right={
          <HeaderActions className="max-w-[70vw]">
            <HeaderModePill ariaLabel="Task mode" value={selection.taskMode ?? 'agent'}
              options={TASK_MODE_SLIDER} onChange={(v) => applySelection({ taskMode: v as TaskMode })} />
            <HeaderModePill ariaLabel="Permission mode" value={selection.approval ?? 'normal'}
              options={APPROVAL_SLIDER} onChange={(v) => applySelection({ approval: v as ApprovalMode })} />
            {started && sessionRef.current && (
              <HeaderControl icon={NotebookPen} label="Brief the agent" priority="low" onClick={briefAgent} />
            )}
            {started && sessionRef.current && (
              <HeaderControl icon={FolderCog} label="Working directory" priority="low" onClick={setWorkspaceDir} />
            )}
            {started && sessionRef.current && (
              <HeaderControl icon={Sparkles} label="Save as starter" priority="low" onClick={saveAsTemplate} />
            )}
            <HeaderControl icon={Edit3} label="New chat" variant="primary" priority="primary" onClick={() => navigate('chat/new')} />
            {started && (
              <HeaderControl icon={PanelRight} label="Workspace" active={activityOpen || !!workspacePane} onClick={() => { if (activityOpen || workspacePane) { setActivityOpen(false); setWorkspacePane('') } else setWorkspacePane('activity') }} />
            )}
            {!started && (
              <HeaderControl icon={History} label="Chat history" active={historyOpen} onClick={() => setHistoryOpen(!historyOpen)} />
            )}
          </HeaderActions>} />

      {
}
      <div className="relative flex min-h-0 flex-1">
        <div className="relative flex min-w-0 flex-1 flex-col">
          {loadingHistory ? (
            <>
              <div className="relative flex-1 overflow-y-auto">
                <MessagesSkeleton />
              </div>
              <div className="relative shrink-0 px-l pb-l">
                <div className="mx-auto flex flex-col items-center" style={{ maxWidth: 'var(--content-width)' }}>
                  {stage}
                </div>
              </div>
            </>
          ) : !started ? (
            <div className="gideon-chat-welcome relative flex-1 flex flex-col items-center justify-center px-l">
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={spring.spatialDefault}
                className="gideon-chat-intro flex items-center gap-l mb-2xl">
                <div className="gideon-chat-emblem"><GideonMark size={44} /></div>
                <div>
                  <p className="gideon-chat-eyebrow" data-type="label-s">Gideon workspace</p>
                  <h1 data-type="display-s" className="text-on-surface">{greeting(name)}</h1>
                </div>
              </motion.div>
              <div className="flex w-full flex-col items-center gap-2xl" style={{ maxWidth: 'var(--content-width)' }}>
                {stage}
                <StarterChips onPick={applyTemplate} />
                <SuggestionChips onPick={(s) => setInput(s)} />
              </div>
            </div>
          ) : (
            <>
              <div className="relative min-h-0 flex-1">
                <div ref={scrollRef} className="absolute inset-0 overflow-y-auto">
                  <AnimatePresence>
                  {
}
                  {findOpen && (
                    <FindBar items={turns} segmentsOf={findSegments} nodeOf={(_t, i) => turnNodes.current.get(i)}
                      scrollRef={scrollRef} label="Find in conversation" onClose={() => setFindOpen(false)} />
                  )}
                  </AnimatePresence>
                  <SelectionQuote scrollRef={scrollRef} onQuote={quoteToComposer} attributionFor={attributionForNode} />
                  <div className="mx-auto flex flex-col gap-2xl px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}>
                  {turns.map((turn, i) => {
                    const isLast = i === turns.length - 1
                    const turnTextOf = (t: ChatTurn) => t.segments.map((s) => (s.kind === 'text' ? s.text : '')).join('')
                    return (
                      <div key={i} className="relative"
                        ref={(el) => { if (el) turnNodes.current.set(i, el); else turnNodes.current.delete(i) }}>
                        {
}
                        {isLast && streaming && (
                          <div ref={glowAnchorRef} aria-hidden className="pointer-events-none absolute left-1/2 -top-2 size-px -translate-x-1/2" />
                        )}
                        {turn.role === 'user' ? (
                          editingTurn === i ? (
                            <UserEditor initial={turnTextOf(turn)} onCancel={() => setEditingTurn(null)} onSubmit={(v) => editResend(i, v)} />
                          ) : (
                            <div className="group/msg">
                              <MessageUser fromComposer={isLast} onFileClick={setOpenFile} pastes={turn.pastes} optimized={turn.optimized}>{turnTextOf(turn)}</MessageUser>
                              {turn.files && turn.files.length > 0 && <TurnAttachments paths={turn.files} onOpenFile={setOpenFile} />}
                              {turn.rewound && turn.rewound.length > 0 && (
                                <RewindDivider snapshots={turn.rewound} canFork={memoryMode === 'persistent'} onFork={(si) => forkRewound(i, si)} />
                              )}
                              {!streaming && <UserActions text={turnTextOf(turn)} canFork={memoryMode === 'persistent'}
                                canRewind={!isLast} onRewind={() => rewindTo(i)} ts={stampOf(turn)}
                                onEdit={() => setEditingTurn(i)} onFork={() => forkAt(i)} />}
                            </div>
                          )
                        ) : (
                          <MessageAssistant actions={!(isLast && streaming) && (
                            <AssistantActions text={turnText(turn)} isLast={isLast} canFork={memoryMode === 'persistent'}
                              variantCount={turn.variantCount} variantIdx={turn.variantIdx} ts={stampOf(turn)}
                              onCopy={() => {}} onRegenerate={regenerate} onFork={() => forkAt(i)}
                              onSwitchVariant={isLast ? switchVariant : undefined}
                              speaking={speakingTurn === i} onSpeak={() => speak(turnText(turn), i)} />
                          )}>
                            <AssistantSegments segments={turn.segments} isLast={isLast} messageTs={turn.ts} streaming={isLast && streaming} onApprove={approve} onSwitchToAgent={switchToAgentAndRun} onOpenFile={setOpenFile} onSetupModel={() => navigate(MODELS_PATH)} chatSessionKey={sessionRef.current ?? undefined} citations={turn.citations} skillsUsed={turn.skillsUsed} />
                          </MessageAssistant>
                        )}
                        {
}
                        {turn.role === 'assistant' && isLast && !streaming && followups.length > 0 && (
                          <FollowupChips items={followups} onPick={(t) => { setInput(t); setFollowups([]) }} onSend={(t) => { setFollowups([]); void send(t) }} />
                        )}
                        { }
                        {turn.role === 'assistant' && isLast && !streaming && checkWorkOffer && (
                          <CheckWorkChip label={checkWorkOffer.label}
                            onRun={() => { const p = checkWorkOffer.prompt; setCheckWorkOffer(null); void send(p) }} />
                        )}
                      </div>
                    )
                  })}
                  <AnimatePresence>
                    {streaming && showThinking && (
                      <StreamingIndicator statusText={statusText} activity={latestActivity} />
                    )}
                  </AnimatePresence>
                  <div ref={endRef} />
                  {
}
                  <div aria-live="polite" className="sr-only">{srAnnounce}</div>
                  {
}
                  <div role="status" aria-live="polite" className="sr-only">
                    {followupAnnouncement(streaming ? 0 : followups.length)}
                  </div>
                  </div>
                </div>
                <SessionMarkerRail turns={turns} scrollRef={scrollRef} nodeOf={nodeForTurn} onJumpTo={jumpToTurn}
                  showReturnToNewest={scrolledUp}
                  onReturnToNewest={() => endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })} />
              </div>
              <div className="relative shrink-0 px-l pb-l">
                {
}
                <AnimatePresence>
                  {!wsConnected && (
                    <motion.div role="status"
                      initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={spring.spatialFast}
                      className="absolute left-1/2 -top-10 z-20 -translate-x-1/2 inline-flex items-center gap-1.5 rounded-pill border bg-surface/95 px-3 h-8 text-[0.75rem] shadow-md backdrop-blur-md"
                      style={{ color: 'var(--color-warn)', borderColor: 'color-mix(in srgb, var(--color-warn) 40%, transparent)' }}>
                      <Loader2 size={13} className="animate-spin" /> Reconnecting…
                    </motion.div>
                  )}
                </AnimatePresence>
                <div className="mx-auto flex flex-col items-center" style={{ maxWidth: 'var(--content-width)' }}>
                  {stage}
                </div>
              </div>
            </>
          )}
        </div>

        {
}
        <AnimatePresenceFilePanel path={openFile} onClose={() => setOpenFile(null)}
          commentTarget={sameSessionTarget((msg) => { send(msg) })} />

        {
}
        <AnimatePresence>
          {(activityOpen || !!workspacePane) && started && sessionRef.current && (
            <SidePanel title="Workspace" icon={<Activity size={18} className="text-primary" />} storeKey="chat-workspace-w"
              fillHeight onClose={() => { setActivityOpen(false); setWorkspacePane('') }}>
              <SessionWorkspace sessionKey={sessionRef.current} pane={workspacePane} onPane={setWorkspacePane}
                turns={turns} activity={activity} onOpenFile={setOpenFile}
                onOpenArtifact={(slug) => navigate(`artifacts/${encodeURIComponent(slug)}`)}
                subagents={subagents} onKillFanout={killFanout}
                side={{ msgs: sideMsgs, busy: sideBusy, onAsk: askSide, onOpen: openSide }} />
            </SidePanel>
          )}
        </AnimatePresence>
        {
}
        <AnimatePresence>
          {historyOpen && !started && (
            <SidePanel title="Chat history" icon={<History size={18} className="text-primary" />} storeKey="chat-history-w"
              fillHeight urlKey={{ key: 'history', setQuery }} onClose={() => setHistoryOpen(false)}>
              <ChatHistorySidePanelBody navigate={navigate} onOpen={(key) => navigate(`chat/${key}`)} />
            </SidePanel>
          )}
        </AnimatePresence>
      </div>
      {resultRef && (
        <Modal title={`${resultToolRef.current || 'Tool'} — full result`} onClose={() => setResultRef('')}>
          <div className="flex flex-col gap-2 p-l" style={{ minWidth: 520, maxWidth: 900 }}>
            {resultBody === null ? (
              <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem]"><Loader2 size={13} className="animate-spin" /> Loading…</div>
            ) : (
              <>
                {resultBody.length > 0 && (
                  <div className="text-on-surface-low text-[0.75rem]">{resultBody.length.toLocaleString()} chars</div>
                )}
                <pre className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-3 py-2 font-mono text-on-surface-var text-[0.75rem] leading-relaxed">{resultBody.content}</pre>
              </>
            )}
          </div>
        </Modal>
      )}
    </div>
  )
}

function AnimatePresenceFilePanel({ path, onClose, commentTarget }: { path: string | null; onClose: () => void; commentTarget?: CommentTarget }) {
  return (
    <AnimatePresence>
      {path && <ChatFilePanel path={path} onClose={onClose} commentTarget={commentTarget} />}
    </AnimatePresence>
  )
}

function TurnAttachments({ paths, onOpenFile }: { paths: string[]; onOpenFile: (p: string) => void }) {
  const [peek, setPeek] = useState<string | null>(null)
  const base = (p: string) => (p.replace(/\/+$/, '').split('/').pop() || p).replace(/^[0-9a-f]{32}_/, '')
  return (
    <div className="mt-1.5 flex flex-wrap justify-end gap-1.5">
      {paths.map((p) => (
        <button key={p} type="button" onClick={() => setPeek(p)} title={`Preview ${base(p)}`}
          className="inline-flex items-center gap-1.5 rounded-pill border border-outline-variant/50 bg-surface-container px-2.5 py-1 text-[0.75rem] text-on-surface-var transition-colors hover:bg-surface-high hover:text-on-surface">
          <Paperclip size={11} className="shrink-0 text-on-surface-low" />
          <span className="max-w-[200px] truncate">{base(p)}</span>
        </button>
      ))}
      {peek && <AttachmentPeekModal path={peek} name={base(peek)} onOpenFile={onOpenFile} onClose={() => setPeek(null)} />}
    </div>
  )
}

function AttachmentPeekModal({ path, name, onOpenFile, onClose }: { path: string; name: string; onOpenFile: (p: string) => void; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let alive = true
    setLoading(true)
    api.attachmentExtract(path)
      .then((r) => { if (alive) setText(r.text || '') })
      .catch(() => { if (alive) setText('') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [path])
  return (
    <Modal title={name} icon={<Paperclip size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <Button variant="ghost-accent" size="sm" onClick={() => { onOpenFile(path); onClose() }}
          className="self-start border border-outline-variant/50">
          <ExternalLink size={14} /> Open original file
        </Button>
        <div>
          <div className="mb-1 text-on-surface-low text-[0.75rem] uppercase tracking-wide">Extracted content (what the agent saw)</div>
          {loading ? (
            <div className="flex items-center gap-2 text-on-surface-low text-[0.8125rem] py-3"><Loader2 size={14} className="animate-spin" /> Extracting…</div>
          ) : text ? (
            <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-m py-2 font-mono text-on-surface-var text-[0.75rem] leading-relaxed">{text}</pre>
          ) : (
            <p className="text-on-surface-low text-[0.8125rem]">No extractable text content (e.g. an image with no OCR configured).</p>
          )}
        </div>
      </div>
    </Modal>
  )
}

function MentionChips({ paths, onRemove, onOpen }: { paths: string[]; onRemove: (p: string) => void; onOpen: (p: string) => void }) {
  const [expanded, setExpanded] = useState<string | null>(null)
  if (!paths.length) return null
  const base = (p: string) => (p.replace(/\/+$/, '').split('/').pop() || p).replace(/^[0-9a-f]{32}_/, '')
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {paths.map((p) => {
        const open = expanded === p
        return (
          <div key={p} className="flex items-center gap-1.5 rounded-lg border border-primary/40 bg-primary/10 px-2.5 py-1.5 text-[0.8125rem]"
            style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
            <FileText size={13} className="shrink-0 text-primary" />
            {
}
            <button type="button" aria-expanded={open} onClick={() => setExpanded(open ? null : p)}
              title={open ? 'Collapse' : 'Show full path'}
              className="min-w-0 text-left font-mono text-on-surface">
              {open ? <span className="break-all">{p}</span> : base(p)}
            </button>
            {open && (
              <Button variant="ghost-accent" size="xs" title="Open file" onClick={() => onOpen(p)}
                className="shrink-0 h-6 px-1.5 text-[0.75rem]">Open</Button>
            )}
            <IconButton icon={X} label="Remove file" onClick={() => onRemove(p)} size={20} iconSize={13}
              tone="danger" className="shrink-0" />
          </div>
        )
      })}
    </div>
  )
}

function ArtifactContextPicker({ attached, onPick, onRemove, onClose }: {
  attached: { slug: string; name: string }[]
  onPick: (a: { slug: string; name: string }) => void
  onRemove: (slug: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const { data, loading, error: artifactsError } = useQuery('artifacts:chat-picker', () => api.artifacts())
  const all = data ?? []
  const attachedSlugs = new Set(attached.map((a) => a.slug))
  const n = q.trim().toLowerCase()
  const matching = n ? all.filter((a) => `${a.name} ${a.slug} ${a.kind}`.toLowerCase().includes(n)) : all
  const shown = matching.slice(0, 40)
  return (
    <Modal title="Reference an artifact" icon={<Boxes size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m" style={{ minWidth: 420 }}>
        <SearchField value={q} onChange={setQ} autoFocus placeholder="Search your artifacts…"
          ariaLabel="Search your artifacts"
          trailingSlot={loading ? <Loader2 size={15} className="animate-spin text-on-surface-low" /> : undefined} />
        {
}
        <ResultAnnouncement count={shown.length} noun="artifacts" active={!!n} />
        {attached.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {attached.map((a) => (
              <Button key={a.slug} variant="ghost" size="xs" onClick={() => onRemove(a.slug)}
                title="Remove from this prompt">
                <Boxes size={11} /> {a.name} <X size={11} />
              </Button>
            ))}
          </div>
        )}
        {data === undefined && artifactsError ? (
          <FieldError>Couldn't load your artifacts — {(artifactsError as Error)?.message || 'the server did not respond'}</FieldError>
        ) : all.length === 0 && !loading ? (
          <p className="text-on-surface-low text-[0.8125rem]">
            No artifacts yet. Ask in chat for a widget or a document and it lands here.
          </p>
        ) : (
          <div className="flex max-h-80 flex-col gap-1 overflow-y-auto">
            {shown.map((a) => {
              const on = attachedSlugs.has(a.slug)
              return (
                <MenuRow key={a.slug} icon={<Boxes size={14} />} label={a.name}
                  hint={`${a.kind} · v${a.version} · ${a.slug}`} selected={on}
                  onClick={() => (on ? onRemove(a.slug) : onPick({ slug: a.slug, name: a.name }))} />
              )
            })}
             {n && shown.length === 0 && (
              <p className="px-2 py-2 text-on-surface-low text-[0.8125rem]">No artifact matches that.</p>
             )}
             <MoreRow total={matching.length} shown={40} noun="artifacts" />
           </div>
        )}
      </div>
    </Modal>
  )
}

function KnowledgeContextPicker({ attached, onPick, onRemove, onClose }: {
  attached: { id: string; name: string }[]
  onPick: (item: { id: string; name: string }) => void
  onRemove: (id: string) => void
  onClose: () => void
}) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<import('../shared/data/api').KnowledgeContextResult | null>(null)
  const [loading, setLoading] = useState(false)
  const MAX = 4000
  const attachedIds = new Set(attached.map((a) => a.id))
  useEffect(() => {
    const query = q.trim()
    if (!query) { setRes(null); return }
    let alive = true
    setLoading(true)
    const t = window.setTimeout(() => {
      api.knowledgeSearchForContext(query, MAX).then((r) => { if (alive) setRes(r) }).catch(() => { if (alive) setRes(null) }).finally(() => { if (alive) setLoading(false) })
    }, 250)
    return () => { alive = false; clearTimeout(t) }
  }, [q])
  const attachedTokens = (res?.results ?? []).filter((r) => attachedIds.has(r.id)).reduce((n, r) => n + r.tokens, 0)
  const pct = Math.min(100, Math.round((attachedTokens / MAX) * 100))
  return (
    <Modal title="Add knowledge to prompt" icon={<BookText size={18} className="text-primary" />} onClose={onClose}>
      <div className="flex flex-col gap-m" style={{ minWidth: 420 }}>
        <SearchField value={q} onChange={setQ} autoFocus placeholder="Search your knowledge library…"
          ariaLabel="Search your knowledge library"
          trailingSlot={loading ? <Loader2 size={15} className="animate-spin text-on-surface-low" /> : undefined} />
        {
}
        <ResultAnnouncement count={res?.results.length ?? 0} noun="knowledge items"
          active={!!q.trim() && !loading && res !== null} />
        {attached.length > 0 && (
          <div className="flex flex-col gap-1.5">
            <div className="flex items-center justify-between text-[0.75rem] text-on-surface-low">
              <span>{attached.length} attached{attachedTokens ? ` · ~${attachedTokens} tokens` : ''}</span>
              {attachedTokens > 0 && <span className="tabular-nums">{pct}% of {MAX}</span>}
            </div>
            {attachedTokens > 0 && (
              <Meter size="thin" label="Prompt budget used by attached knowledge" pct={pct}
                tone={pct > 90 ? 'var(--color-warn)' : 'var(--color-primary)'} />
            )}
          </div>
        )}
        {
}
        <div role="group" aria-label="Knowledge to attach" className="max-h-[46vh] overflow-y-auto flex flex-col gap-1.5">
          {loading && !res ? <div className="grid place-items-center py-6 text-on-surface-low"><Loader2 size={16} className="animate-spin" /></div>
            : !q.trim() ? <p className="py-6 text-center text-on-surface-low text-[0.8125rem]">Type to search notes, gists, bookmarks, docs…</p>
            : (res?.results.length ?? 0) === 0 ? <p className="py-6 text-center text-on-surface-low text-[0.8125rem]">No matches for “{q}”.</p>
            : res!.results.map((r) => {
              const on = attachedIds.has(r.id)
              return (
                <button key={r.id} type="button" aria-pressed={on}
                  onClick={() => on ? onRemove(r.id) : onPick({ id: r.id, name: r.title })}
                  className="flex items-start gap-2 rounded-lg px-3 py-2 text-left transition-colors"
                  style={on ? { background: 'color-mix(in srgb, var(--color-primary) 12%, transparent)', outline: '1px solid color-mix(in srgb, var(--color-primary) 40%, transparent)' } : { background: 'var(--color-surface-container)' }}>
                  <span className="mt-0.5 shrink-0 grid size-4 place-items-center rounded border" style={on ? { background: 'var(--color-primary)', borderColor: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { borderColor: 'var(--color-outline-variant)' }}>{on && <Check size={11} />}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="min-w-0 truncate text-on-surface text-[0.8125rem]" style={fvs(500)}>{r.title}</span>
                      <span className="ml-auto shrink-0 tabular-nums text-on-surface-low text-[0.75rem]">~{r.tokens} tok</span>
                    </div>
                    {r.summary && <div className="mt-0.5 line-clamp-2 text-on-surface-low text-[0.75rem]">{r.summary}</div>}
                  </div>
                </button>
              )
            })}
        </div>
        <div className="flex justify-end"><Button size="sm" onClick={onClose}>Done</Button></div>
      </div>
    </Modal>
  )
}

function KnowledgeChips({ items, onRemove }: { items: { id: string; name: string }[]; onRemove: (id: string) => void }) {
  if (!items.length) return null
  return (
    <div className="mb-2 flex flex-wrap gap-2">
      {items.map((k) => (
        <div key={k.id} className="flex items-center gap-1.5 rounded-lg border border-primary/40 px-2.5 py-1.5 text-[0.8125rem]"
          style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>
          <BookText size={13} className="shrink-0 text-primary" />
          <span className="min-w-0 truncate text-on-surface" title={k.name}>{k.name}</span>
          <IconButton icon={X} label="Remove knowledge reference" onClick={() => onRemove(k.id)} size={20} iconSize={13}
            tone="danger" className="shrink-0" />
        </div>
      ))}
    </div>
  )
}

function QueueStack({ items, onCancel, onEdit, onInterrupt, canInterrupt = false }: {
  items: { id: string; content: string }[]
  onCancel: (id: string) => void
  onEdit: (id: string, content: string) => void
  onInterrupt?: (id: string) => void
  canInterrupt?: boolean
}) {
  const reduce = useReducedMotion()
  const [expanded, setExpanded] = useState(false)
  if (!items.length) return null
  const stacked = !reduce && items.length > 1 && !expanded
  const peekY = expr(7, 0.4)
  const peekScale = expr(0.04, 0.5)
  const maxPeek = 3

  const header = (
    <button type="button" onClick={() => items.length > 1 && setExpanded((e) => !e)}
      className={`flex items-center gap-1.5 px-1 text-[0.75rem] uppercase tracking-wide text-on-surface-low ${items.length > 1 ? 'hover:text-on-surface-var' : 'cursor-default'}`}>
      <Clock size={11} className="shrink-0" /> {items.length} queued · sent one at a time as each turn finishes
      {items.length > 1 && <ChevronDown size={11} className={`shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />}
    </button>
  )

  const card = (q: { id: string; content: string }, i: number, depth: number) => (
    <motion.div key={q.id} layout
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={stacked
        ? { opacity: depth === 0 ? 1 : Math.max(0, 1 - depth * 0.28), y: -depth * peekY, scale: 1 - depth * peekScale }
        : { opacity: 1, y: 0, scale: 1 }}
      exit={reduce ? undefined : { opacity: 0, y: 8, transition: spring.spatialFast }}
      transition={spring.spatialDefault}
      style={stacked ? { position: depth === 0 ? 'relative' : 'absolute', insetInline: 0, top: 0, zIndex: maxPeek - depth } : undefined}
      className="group/q flex items-center gap-2 rounded-lg border border-outline-variant/50 bg-surface-high/60 px-2.5 py-1.5 text-[0.8125rem]">
      <span className="shrink-0 tabular-nums text-on-surface-low">{i + 1}</span>
      <span className="min-w-0 flex-1 truncate text-on-surface" title={q.content}>{q.content}</span>
      { }
      {(!stacked || depth === 0) && (
        <span className="flex shrink-0 items-center gap-0.5">
          {canInterrupt && onInterrupt && (
            <IconButton icon={PlayCircle} label="Interrupt now — stop the current turn and run this next" onClick={() => onInterrupt(q.id)} size={20} iconSize={13}
              className="opacity-0 transition-opacity hover:text-primary group-hover/q:opacity-100 focus-within:opacity-100" />
          )}
          <IconButton icon={Pencil} label="Edit queued message" onClick={() => onEdit(q.id, q.content)} size={20} iconSize={12}
            className="opacity-0 transition-opacity hover:text-primary group-hover/q:opacity-100 focus-within:opacity-100" />
          <IconButton icon={X} label="Cancel queued message" onClick={() => onCancel(q.id)} size={20} iconSize={13}
            tone="danger" />
        </span>
      )}
    </motion.div>
  )

  return (
    <div className="mb-2 flex flex-col gap-1.5">
      {header}
      {stacked ? (
        <div className="relative" style={{ paddingTop: Math.min(items.length - 1, maxPeek) * peekY }}>
          {items.slice(0, maxPeek + 1).map((q, i) => card(q, i, i)).reverse()}
        </div>
      ) : (
        <AnimatePresence initial={false}>
          <div className="flex flex-col gap-1.5">{items.map((q, i) => card(q, i, 0))}</div>
        </AnimatePresence>
      )}
    </div>
  )
}

function MessagesSkeleton() {
  const rows = [
    { me: true, w: 'w-1/3' }, { me: false, w: 'w-3/4' },
    { me: true, w: 'w-2/5' }, { me: false, w: 'w-2/3' },
  ]
  return (
    <div className="mx-auto flex flex-col gap-2xl px-l py-2xl" style={{ maxWidth: 'var(--content-width)' }}
      role="status" aria-busy="true" >
        <LoadingStatus what="conversation" />
      {rows.map((r, i) => (
        <div key={i} className={`flex flex-col gap-2 ${r.me ? 'items-end' : 'items-start'}`}>
          <Skeleton className={`h-4 ${r.w} ${r.me ? 'max-w-[70%]' : ''}`} />
          {!r.me && <><Skeleton className="h-4 w-11/12" /><Skeleton className="h-4 w-4/5" /></>}
        </div>
      ))}
    </div>
  )
}

function PasteCards({ blocks, onRemove }: { blocks: PasteBlock[]; onRemove: (seq: number) => void }) {
  const [preview, setPreview] = useState<PasteBlock | null>(null)
  if (!blocks.length) return null
  return (
    <>
      <div className="mb-2 flex flex-wrap gap-2">
        {blocks.map((b) => (
          <div key={b.id} className="group flex items-center gap-2 rounded-lg border border-outline-variant/50 bg-surface-container px-2.5 py-1.5">
            <Clipboard size={13} className="shrink-0 text-primary" />
            <button type="button" onClick={() => setPreview(b)} className="text-left text-on-surface text-[0.8125rem] hover:underline">
              Paste #{b.seq} <span className="text-on-surface-low">· {b.lines} line{b.lines === 1 ? '' : 's'}</span>
            </button>
            <IconButton icon={X} label={`Remove paste #${b.seq}`} onClick={() => onRemove(b.seq)} size={20} iconSize={13}
              tone="danger" className="shrink-0" />
          </div>
        ))}
      </div>
      <AnimatePresence>
        {preview && (
          <Modal title={`Paste #${preview.seq} · ${preview.lines} lines`} icon={<Clipboard size={18} className="text-primary" />} onClose={() => setPreview(null)}>
            <pre className="overflow-auto whitespace-pre-wrap rounded-md bg-surface-low px-m py-s font-mono text-on-surface-var text-[0.8125rem] leading-relaxed">{preview.content}</pre>
          </Modal>
        )}
      </AnimatePresence>
    </>
  )
}

function RewindDivider({ snapshots, canFork, onFork }: {
  snapshots: NonNullable<ChatTurn['rewound']>
  canFork: boolean
  onFork: (snapshotIndex?: number) => void
}) {
  const [open, setOpen] = useState(false)
  const latest = snapshots[snapshots.length - 1]
  const kept = Math.max(0, (latest?.messages?.length ?? 0) - 1)
  return (
    <div className="mt-2 flex flex-col items-end gap-1.5">
      <div className="flex items-center gap-2 text-on-surface-low text-[0.75rem]">
        <Rewind size={12} className="shrink-0" />
        <span>Rewound from here · {kept} message{kept === 1 ? '' : 's'} kept in history</span>
        <QuietButton onClick={() => setOpen((o) => !o)} ariaExpanded={open} className="h-6">
          {open ? 'Hide' : 'View'} <ChevronDown size={11} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
        </QuietButton>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast}
            className="w-full max-w-[452px] overflow-hidden rounded-xl border border-outline-variant/50 bg-surface-container/60">
            <div className="flex items-center justify-between border-b border-outline-variant/40 px-3 py-2">
              <span className="text-on-surface-low text-[0.6875rem] uppercase tracking-wide">Retained history (read-only)</span>
              {canFork && (
                <Button size="sm" variant="ghost" onClick={() => onFork()} className="h-6 px-2 text-[0.75rem]">
                  <GitBranch size={12} /> Restore as fork
                </Button>
              )}
            </div>
            <div className="flex flex-col gap-2 px-3 py-2.5">
              {(latest?.messages ?? []).map((m, mi) => (
                <div key={mi} className={`text-[0.8125rem] leading-relaxed ${m.role === 'user' ? 'text-on-surface-var' : 'text-on-surface-low'}`}>
                  <span className="mr-1.5 text-on-surface-low text-[0.6875rem] uppercase tracking-wide">{m.role === 'user' ? 'You' : 'Assistant'}</span>
                  <span className="whitespace-pre-wrap">{m.content.length > 400 ? m.content.slice(0, 400) + '…' : m.content}</span>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function UserEditor({ initial, onSubmit, onCancel }: { initial: string; onSubmit: (v: string) => void; onCancel: () => void }) {
  const [v, setV] = useState(initial)
  return (
    <div className="flex flex-col items-end gap-2">
      <textarea autoFocus value={v} onChange={(e) => setV(e.target.value)} rows={Math.min(10, v.split('\n').length + 1)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') { e.preventDefault(); onCancel() }
          else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); onSubmit(v) }
        }}
        className="w-full resize-none rounded-2xl bg-surface-container px-5 py-4 text-on-surface text-[1.0625rem] leading-relaxed outline-none focus:ring-2 focus:ring-inset focus:ring-primary"
        style={{ maxWidth: 452 }} />
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel} className="px-3 text-on-surface-low">Cancel</Button>
        <Button size="sm" onClick={() => onSubmit(v)} disabled={!v.trim()} className="px-4"
          disabledReason={!v.trim() ? 'The message cannot be empty' : undefined}>Resend</Button>
      </div>
    </div>
  )
}

function SelectionQuote({ scrollRef, onQuote, attributionFor }: {
  scrollRef: React.RefObject<HTMLDivElement | null>
  onQuote: (text: string, attribution?: string) => void
  attributionFor: (node: Node | null) => string | undefined
}) {
  const [pos, setPos] = useState<{ x: number; y: number; text: string; attribution?: string } | null>(null)
  const barRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const root = scrollRef.current
    if (!root) return
    const recompute = () => {
      const sel = window.getSelection()
      const text = sel?.toString().trim() ?? ''
      if (!text || !sel || sel.rangeCount === 0) { setPos(null); return }
      const range = sel.getRangeAt(0)
      if (!root.contains(range.commonAncestorContainer)) { setPos(null); return }
      const r = range.getBoundingClientRect()
      const pr = root.getBoundingClientRect()
      setPos({
        x: r.left - pr.left + root.scrollLeft + r.width / 2,
        y: r.top - pr.top + root.scrollTop - 8,
        text,
        attribution: attributionFor(range.commonAncestorContainer),
      })
    }
    const onUp = (e: MouseEvent) => {
      if (barRef.current && e.target instanceof Node && barRef.current.contains(e.target)) return
      recompute()
    }
    let raf = 0
    const onSelChange = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const sel = window.getSelection()
        if (!sel || !sel.toString().trim()) { setPos(null); return }
        recompute()
      })
    }
    const onDown = (e: MouseEvent) => {
      if (barRef.current && e.target instanceof Node && barRef.current.contains(e.target)) return
      setPos(null)
    }
    document.addEventListener('mouseup', onUp)
    document.addEventListener('selectionchange', onSelChange)
    root.addEventListener('mousedown', onDown)
    return () => {
      cancelAnimationFrame(raf)
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('selectionchange', onSelChange)
      root.removeEventListener('mousedown', onDown)
    }
  }, [scrollRef, attributionFor])
  if (!pos) return null
  const clear = () => { setPos(null); window.getSelection()?.removeAllRanges() }
  return (
    <SelectionToolbar ref={barRef} x={pos.x} y={pos.y} actions={[
      { icon: Quote, label: 'Quote', onPress: () => { onQuote(pos.text, pos.attribution); clear() } },
      { icon: Clipboard, label: 'Copy', onPress: () => { void copyText(pos.text, 'the selection'); clear() } },
    ]} />
  )
}

function AssistantSegments({ segments, isLast, messageTs, streaming, onApprove, onSwitchToAgent, onOpenFile, onSetupModel, chatSessionKey, citations, skillsUsed }: {
  segments: Segment[]; isLast: boolean
  messageTs?: string
  streaming?: boolean
  onApprove: (id: string, action: ApproveAction, revision?: string) => void
  onSwitchToAgent: (continuation: string) => void
  onOpenFile: (path: string) => void
  onSetupModel: () => void
  chatSessionKey?: string
  citations?: MemoryCitation[]
  skillsUsed?: SkillUsed[]
}) {
  const fullText = segments.filter((s) => s.kind === 'text').map((s) => (s as { text: string }).text).join('\n')
  const { switchTo } = parseSwitchToAgent(fullText)

  const ledger: { fed?: string; learned?: string; learnedOrigin?: string; stats?: string } = {}
  for (const s of segments) {
    if (s.kind !== 'activity') continue
    const ak = (s as ActivitySegment).activityKind
    if (ak === 'context') ledger.fed = (s as ActivitySegment).text
    else if (ak === 'learned') {
      ledger.learned = (s as ActivitySegment).text
      ledger.learnedOrigin = (s as ActivitySegment).origin
    }
    else if (ak === 'stats') ledger.stats = (s as ActivitySegment).text
  }
  const hasLedger = Boolean(ledger.fed || ledger.learned || ledger.stats)

  const renderItem = (seg: Segment, i: number): React.ReactNode => {
    if (seg.kind === 'tool') {
      const t = seg as ToolSegment
      const sdlc = t.done ? sdlcRefFromTool(t.tool, t.output) : null
      if (sdlc) return <SdlcProgressCard key={seg.id || i} refObj={sdlc} />
      const wf = t.done ? workflowRefFromTool(t.tool, t.output) : null
      if (wf) return <WorkflowProgressCard key={seg.id || i} refObj={wf} />
      return <ToolCard key={seg.id || i} seg={t} />
    }
    if (seg.kind === 'activity') return <ActivityLine key={i} seg={seg as ActivitySegment} />
    if (seg.kind === 'thinking') return <ThinkingBlock key={i} text={(seg as ThinkingSegment).text} defaultOpen={streaming} />
    if (seg.kind === 'error') {
      const text = (seg as { text: string }).text
      return isNoModelSetupError(text)
        ? <NoModelSetupState key={i} detail={text} onSetup={onSetupModel} />
        : <InlineError key={i} icon multiline className="my-1">{text}</InlineError>
    }
    if (seg.kind === 'approval') {
      const ap = seg as ApprovalSegment
      return <ApprovalCard key={ap.id || i} seg={ap} onAct={onApprove} />
    }
    if (seg.kind === 'text') {
      const body = parseSwitchToAgent(parseOptions(seg.text).body).body
      return body ? <Markdown key={i} onFileClick={onOpenFile} chatSessionKey={chatSessionKey} messageTs={messageTs} streaming={streaming} citations={citations}>{body}</Markdown> : null
    }
    return null
  }
  const isProcess = (s: Segment) =>
    s.kind === 'tool' || s.kind === 'error' || s.kind === 'approval' ||
    (s.kind === 'activity' && !['context', 'learned', 'stats'].includes((s as ActivitySegment).activityKind || ''))

  const processIdxs = segments.flatMap((s, i) => (isProcess(s) ? [i] : []))
  const lastProcessIdx = processIdxs.length ? processIdxs[processIdxs.length - 1] : -1
  const stepCount = processIdxs.length
  const toolNames = [...new Set(
    segments.filter((s, i) => i <= lastProcessIdx && s.kind === 'tool').map((s) => (s as ToolSegment).tool),
  )]
  const workSegs = lastProcessIdx >= 0 ? segments.slice(0, lastProcessIdx + 1) : []
  const finalSegs = (lastProcessIdx >= 0 ? segments.slice(lastProcessIdx + 1) : segments).filter((s) => s.kind === 'text')

  const isSdlc = (s: Segment) => s.kind === 'tool'
    && !!(s as ToolSegment).done && !!sdlcRefFromTool((s as ToolSegment).tool, (s as ToolSegment).output)
  const isWorkflow = (s: Segment) => s.kind === 'tool'
    && !!(s as ToolSegment).done && !!workflowRefFromTool((s as ToolSegment).tool, (s as ToolSegment).output)
  const isLiveCard = (s: Segment) => isSdlc(s) || isWorkflow(s)
  const sdlcNodes = segments.filter(isLiveCard).map(renderItem).filter(Boolean)
  const workNodes = workSegs.filter((s) => !isLiveCard(s)).map(renderItem).filter(Boolean)
  const finalNodes = finalSegs.map(renderItem).filter(Boolean)
  const hasFinal = finalNodes.length > 0
  const collapseWork = !streaming && hasFinal && stepCount > 0

  return (
    <>
      {workNodes.length > 0 && (
        collapseWork
          ? <AgentWork stepCount={stepCount} toolNames={toolNames}>{workNodes}</AgentWork>
          : <div className="flex flex-col gap-1">{workNodes}</div>
      )}
      {
}
      {sdlcNodes.length > 0 && <div className="flex flex-col gap-1">{sdlcNodes}</div>}
      {finalNodes}

      {
}
      {skillsUsed && skillsUsed.length > 0 && <SkillsUsedChip skills={skillsUsed} />}

      {hasLedger && <ContextLedger fed={ledger.fed} learned={ledger.learned} learnedOrigin={ledger.learnedOrigin} stats={ledger.stats} />}

      {
}
      {isLast && !streaming && switchTo !== null && (
        <div className="mt-3">
          <Button variant="primary" size="sm" onClick={() => onSwitchToAgent(switchTo)} className="gap-1.5">
            <Bot size={15} strokeWidth={2.2} />
            Switch to Agent &amp; run it
          </Button>
        </div>
      )}

    </>
  )
}

function AgentWork({ stepCount, toolNames, children }: { stepCount: number; toolNames: string[]; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const summary = toolNames.length
    ? `${toolNames.slice(0, 3).join(', ')}${toolNames.length > 3 ? ` +${toolNames.length - 3} more` : ''}`
    : ''
  return (
    <div className="mb-1.5">
      <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="group/work flex w-full items-center gap-1.5 rounded-md py-1 text-left text-on-surface-low/85 text-[0.75rem] transition-colors hover:text-on-surface-low">
        <motion.span animate={{ rotate: open ? 90 : 0 }} transition={spring.spatialFast} className="shrink-0 opacity-60">
          <ChevronRight size={12} />
        </motion.span>
        <Wrench size={12} className="shrink-0 opacity-70" />
        <span className="shrink-0" style={fvs(500)}>
          {open ? 'Hide work' : `Worked through ${stepCount} ${stepCount === 1 ? 'step' : 'steps'}`}
        </span>
        {!open && summary && <span className="min-w-0 truncate text-on-surface-low/60">· {summary}</span>}
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }} exit={{ opacity: 0, height: 0 }}
            transition={spring.spatialFast} className="overflow-hidden">
            <div className="mt-1 ml-1.5 flex flex-col gap-1 border-l border-outline-variant/40 pl-3">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function ActivityLine({ seg }: { seg: ActivitySegment }) {
  return (
    <div className="my-1 flex items-center gap-1.5 text-on-surface-low text-[0.75rem]">
      <Activity size={12} className="shrink-0 opacity-70" /><span>{seg.text}</span>
    </div>
  )
}


function SkillsUsedChip({ skills }: { skills: SkillUsed[] }) {
  const used = skills.filter((s) => s.state === 'admitted' || s.state === 'reduced')
  if (!used.length) return null
  const reduced = used.filter((s) => s.state === 'reduced').length
  return (
    <div className="mt-2 mb-1 flex items-center gap-1.5 text-on-surface-low/80 text-[0.75rem]"
      title={skillsUsedTitle(used)}>
      <Sparkles size={11} className="shrink-0 opacity-70" />
      <span>
        {skillsUsedLabel(used)}
        {reduced > 0 && <span className="opacity-80"> · {reduced} summarized</span>}
      </span>
    </div>
  )
}

function snippetParts(snippet: string): { text: string; hit: boolean }[] {
  const parts: { text: string; hit: boolean }[] = []
  let rest = snippet
  while (rest) {
    const open = rest.indexOf('<<')
    if (open < 0) { parts.push({ text: rest, hit: false }); break }
    const close = rest.indexOf('>>', open + 2)
    if (close < 0) { parts.push({ text: rest, hit: false }); break }
    if (open > 0) parts.push({ text: rest.slice(0, open), hit: false })
    parts.push({ text: rest.slice(open + 2, close), hit: true })
    rest = rest.slice(close + 2)
  }
  return parts
}

function ChatHistoryPage({ navigate, query, setQuery }: { navigate: (p: string) => void; query: Record<string, string>; setQuery: RouteProps['setQuery'] }) {
  const archivedView = (query.archived ?? '') === '1'
  const { data: cachedSessions, error: sessionsError, refresh: refreshSessions } = useQuery<ChatSessionSummary[]>(
    archivedView ? 'chat:sessions:archived' : 'chat:sessions',
    () => api.chatSessions(archivedView),
    { persist: false },
  )
  const { data: foldersData, error: foldersError, refresh: refreshFolders } = useQuery<ChatFolder[]>('chat:folders', () => api.chatFolders(), { persist: true })
  const { data: tagsData, error: tagsError, refresh: refreshTags } = useQuery<ChatTag[]>('chat:tags', () => api.chatTags(), { persist: true })
  const folders = foldersData ?? []
  const tags = tagsData ?? []
  const [optimistic, setSessions] = useState<ChatSessionSummary[] | null>(null)
  useEffect(() => { if (cachedSessions !== undefined) setSessions(cachedSessions) }, [cachedSessions])
  const sessions = optimistic
  const [q, setQ] = useQueryParam(query, setQuery, 'q', '', { replace: true })
  const [viewRaw, setViewRaw] = useQueryParam(query, setQuery, 'view', 'list', { replace: true })
  const view: 'list' | 'board' = viewRaw === 'board' ? 'board' : 'list'
  const setView = (v: 'list' | 'board') => setViewRaw(v)
  const [peekKey, setPeekKey] = useQueryParam(query, setQuery, 'peek', '')
  const peekSession = peekKey ? (sessions ?? []).find((s) => s.key === peekKey) ?? null : null
  const [tagsRaw, setTagsRaw] = useQueryParam(query, setQuery, 'tags', '', { replace: true })
  const tagFilter = useMemo(() => new Set(tagsRaw.split(',').map((t) => t.trim()).filter(Boolean)), [tagsRaw])
  const setTagFilter = (next: Set<string>) => setTagsRaw([...next].join(','))
  const [originRaw, setOriginRaw] = useQueryParam(query, setQuery, 'origin', 'manual', { replace: true })
  const origin: 'manual' | 'loop' | 'code' | 'channel' | 'all' =
    originRaw === 'loop' || originRaw === 'code' || originRaw === 'channel' || originRaw === 'all' ? originRaw : 'manual'
  const setOrigin = (o: 'manual' | 'loop' | 'code' | 'channel' | 'all') => setOriginRaw(o)
  const [archivedRaw, setArchivedRaw] = useQueryParam(query, setQuery, 'archived', '', { replace: true })
  const showArchived = archivedRaw === '1'
  const setShowArchived = (v: boolean) => setArchivedRaw(v ? '1' : '')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkBusy, setBulkBusy] = useState(false)
  const [bulkNote, setBulkNote] = useState('')
  const selecting = selected.size > 0
  const toggleSelected = (key: string) => {
    setBulkNote('')
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }
  const clearSelection = () => setSelected(new Set())

  const runBulk = async (op: 'archive' | 'restore' | 'never_archive', args: { value?: boolean } = {}) => {
    if (!selected.size || bulkBusy) return
    setBulkBusy(true); setBulkNote('')
    try {
      const res = await api.bulkSessions(op, [...selected], args)
      const verb = op === 'archive' ? 'Archived' : op === 'restore' ? 'Restored' : 'Updated'
      const parts = [`${verb} ${res.changed.length}`]
      if (res.unchanged.length) parts.push(`${res.unchanged.length} already set`)
      if (res.missing.length) parts.push(`${res.missing.length} not found`)
      setBulkNote(parts.join(' · '))
      clearSelection()
      load()
    } catch {
      setBulkNote('Bulk action failed — nothing was changed.')
    } finally {
      setBulkBusy(false)
    }
  }

  const load = useCallback(() => {
    invalidateKeys('chat:sessions', true)
    refreshSessions(); refreshFolders(); refreshTags()
  }, [refreshSessions, refreshFolders, refreshTags])

  const [retag, setRetag] = useState<RetagJob | null>(null)
  const retagRunning = retag?.status === 'running'
  const retagUpdatedRef = useRef(0)
  useEffect(() => {
    api.retagStatus().then((j) => { if (j && j.status === 'running') setRetag(j) }).catch(() => {})
  }, [])
  useChatSocket((m: WsMessage) => {
    if (m.type !== 'retag_progress' && m.type !== 'retag_done') return
    const job = m.data as unknown as RetagJob
    setRetag(job)
    const changed = (job.updated ?? 0) !== retagUpdatedRef.current
    retagUpdatedRef.current = job.updated ?? 0
    if (changed || m.type === 'retag_done') { load(); refreshTags() }
    if (m.type === 'retag_done') {
      if (job.status === 'done') notify(`Re-tagged ${job.updated ?? 0} of ${job.total ?? 0} chats`, 'success')
      else if (job.status === 'error') notify(`Re-tagging failed: ${job.error || 'unknown error'}`, 'error')
      else if (job.status === 'cancelled') notify('Re-tagging cancelled', 'info')
    }
  })
  async function startRetag() {
    if (retagRunning) { await api.cancelRetag().catch(reportActionFailure('cancel the retag run')); return }
    if (!(await confirm({
      title: 'Generate tags for all chats?',
      body: 'Every chat is re-read and tags generated: fitting tags added, stale ones corrected, obsolete ones removed. Incognito and temporary chats are never touched.',
      confirmLabel: 'Generate tags',
    }))) return
    try {
      const job = await api.retagAllSessions()
      setRetag(job)
    } catch (e) {
      notify(`Couldn't start re-tagging: ${String((e as Error)?.message || e)}`, 'error')
    }
  }

  const tagById = useMemo(() => { const m: Record<string, ChatTag> = {}; for (const t of tags) m[t.id] = t; return m }, [tags])
  const n = q.trim().toLowerCase()
  const recency = sessionRecencyMs
  const [contentKeys, setContentKeys] = useState<Set<string> | null>(null)
  const [contentSnippets, setContentSnippets] = useState<Map<string, string>>(new Map())
  const [folderDragKey, setFolderDragKey] = useState<string | null>(null)
  const [overFolder, setOverFolder] = useState<string | null>(null)
  useEffect(() => {
    const query = q.trim()
    if (query.length < 2) { setContentKeys(null); setContentSnippets(new Map()); return }
    let alive = true
    const t = window.setTimeout(() => {
      api.sessionsSearch(query).then((rows) => {
        if (!alive) return
        const strip = (k: string) => k.replace(/^dashboard[_:]/, '')
        setContentKeys(new Set(rows.map((r) => strip(r.key))))
        setContentSnippets(new Map(
          rows.filter((r) => r.snippet).map((r) => [strip(r.key), r.snippet as string]),
        ))
      }).catch(() => { if (alive) { setContentKeys(null); setContentSnippets(new Map()) } })
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [q])
  const matches = useCallback((s: ChatSessionSummary) => {
    const sOrigin = s.origin ?? 'manual'
    if (origin !== 'all' && sOrigin !== origin) return false
    if (n) {
      const local = `${s.title} ${s.key} ${s.source_label ?? ''} ${s.prompt_preview ?? ''} ${s.last_message ?? ''}`.toLowerCase().includes(n)
      const inContent = contentKeys?.has(s.key) ?? false
      if (!local && !inContent) return false
    }
    if (tagFilter.size && !(s.tags ?? []).some((t) => tagFilter.has(t))) return false
    return true
  }, [n, tagFilter, origin, contentKeys])
  const filtered = (sessions ?? []).filter(matches).slice()
    .sort((a, b) => (Number(!!b.pinned) - Number(!!a.pinned)) || (recency(b) - recency(a)))
  const originCounts = useMemo(() => {
    const c = { manual: 0, loop: 0, code: 0, channel: 0, all: 0 }
    for (const s of sessions ?? []) {
      c.all++
      const o = s.origin ?? 'manual'
      if (o === 'manual') c.manual++
      else if (o === 'loop') c.loop++
      else if (o === 'code') c.code++
      else if (o === 'channel') c.channel++
    }
    return c
  }, [sessions])

  async function del(s: ChatSessionSummary) {
    if (!(await confirm({
      title: 'Delete chat?',
      body: `"${sessionTitle(s)}" and its history will be permanently removed.`,
      danger: true, confirmLabel: 'Delete',
    }))) return
    try {
      await api.deleteChatSession(s.key)
    } catch (e) {
      notify(`Couldn't delete this chat: ${String((e as Error)?.message || e)}`, 'error')
      return
    }
    invalidateKeys(detailKey(s.key))
    load()
  }
  function downloadExport(key: string, format: 'md' | 'json') {
    const a = document.createElement('a')
    a.href = api.sessionExportUrl(key, format)
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
    notify('Exporting this chat — credentials are redacted from the file.', 'info')
  }
  async function shareSession(s: ChatSessionSummary) {
    try {
      const res = await api.shareSession(s.key)
      notify(`Shared as "${res.name}" — read-only, credentials redacted.`, 'success')
      navigate(`artifacts/${res.slug}`)
    } catch (e) {
      notify(`Couldn't share this chat: ${String((e as Error)?.message || e)}`, 'error')
    }
  }
  async function togglePin(key: string, pinned: boolean) {
    setSessions((prev) => prev && prev.map((s) => (s.key === key ? { ...s, pinned } : s)))
    await api.pinChatSession(key, pinned).catch(() => load())
  }
  async function setFolder(key: string, folderId: string | null) {
    setSessions((prev) => prev && prev.map((s) => (s.key === key ? { ...s, folder_id: folderId || '' } : s)))
    await api.setSessionFolder(key, folderId).catch(() => load())
  }
  async function toggleTag(key: string, tagId: string) {
    const s = (sessions ?? []).find((x) => x.key === key)
    const next = new Set(s?.tags ?? [])
    next.has(tagId) ? next.delete(tagId) : next.add(tagId)
    const arr = [...next]
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, tags: arr } : x)))
    await api.setSessionTags(key, arr).catch(() => load())
  }
  async function setColumnTag(key: string, toTagId: string | null, fromTagId: string | null) {
    const s = (sessions ?? []).find((x) => x.key === key)
    if (!s) return
    const cur = s.tags ?? []
    const next = new Set(cur)
    if (fromTagId) next.delete(fromTagId)
    if (toTagId) next.add(toTagId)
    const arr = [...next]
    if (arr.length === cur.length && cur.every((t) => next.has(t))) return
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, tags: arr } : x)))
    await api.setSessionTags(key, arr).catch(() => load())
  }
  async function setLifecycle(key: string, lifecycle: 'active' | 'archived') {
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, lifecycle } : x)))
    await api.setSessionLifecycle(key, { lifecycle }).catch(
      reportActionFailure(`${lifecycle === 'archived' ? 'archive' : 'unarchive'} this chat`))
    load()
  }
  async function setNeverArchive(key: string, value: boolean) {
    setSessions((prev) => prev && prev.map((x) => (x.key === key ? { ...x, never_archive: value } : x)))
    await api.setSessionLifecycle(key, { never_archive: value }).catch(() => load())
  }
  async function createFolder() {
    const name = await promptInput({ title: 'New folder', label: 'Folder name', placeholder: 'e.g. Research', confirmLabel: 'Create' })
    if (!name) return
    if (!(await reportingWrite(`create the folder "${name}"`, () => api.createChatFolder(name)))) return
    load()
  }

  const byFolder = useMemo(() => {
    const groups: { folder: ChatFolder | null; items: ChatSessionSummary[] }[] = []
    for (const f of folders) groups.push({ folder: f, items: filtered.filter((s) => s.folder_id === f.id) })
    groups.push({ folder: null, items: filtered.filter((s) => !s.folder_id || !folders.some((f) => f.id === s.folder_id)) })
    return groups.filter((g) => g.items.length > 0 || g.folder)
  }, [folders, filtered])

  const card = (s: ChatSessionSummary) => {
    const menuItems: ContextMenuItem[] = [
      { icon: <Eye size={15} />, label: 'Peek', onSelect: () => setPeekKey(s.key) },
      { icon: <MessageSquare size={15} />, label: 'Open', onSelect: () => navigate(`chat/${s.key}`) },
      { icon: <Pin size={15} />, label: s.pinned ? 'Unpin' : 'Pin to top', onSelect: () => togglePin(s.key, !s.pinned) },
      ...(s.folder_id ? [{ icon: <Folder size={15} />, label: 'Remove from folder', onSelect: () => setFolder(s.key, null) }] : []),
      ...folders.filter((f) => f.id !== s.folder_id).map((f) => ({ icon: <Folder size={15} />, label: `Move to ${f.name}`, onSelect: () => setFolder(s.key, f.id) })),
      ...(s.lifecycle === 'archived'
        ? [{ icon: <ArchiveRestore size={15} />, label: 'Restore from archive', onSelect: () => setLifecycle(s.key, 'active') }]
        : [{ icon: <Archive size={15} />, label: 'Archive', onSelect: () => setLifecycle(s.key, 'archived') }]),
      {
        icon: <Pin size={15} />,
        label: s.never_archive ? 'Allow auto-archive' : 'Never auto-archive',
        onSelect: () => setNeverArchive(s.key, !s.never_archive),
      },
      { icon: <Download size={15} />, label: 'Export as Markdown', onSelect: () => downloadExport(s.key, 'md') },
      { icon: <Download size={15} />, label: 'Export as JSON', onSelect: () => downloadExport(s.key, 'json') },
      { icon: <Share2 size={15} />, label: 'Share as read-only artifact', onSelect: () => shareSession(s) },
      { icon: <Trash2 size={15} />, label: 'Delete', danger: true, onSelect: () => del(s) },
    ]
    return (
    <ContextMenu key={s.key} items={menuItems}>
    {
}
    <div role="button" tabIndex={0} onClick={() => setPeekKey(peekKey === s.key ? '' : s.key)}
      draggable
      onDragStart={(e) => { e.dataTransfer.setData('text/plain', s.key); e.dataTransfer.effectAllowed = 'move'; requestAnimationFrame(() => setFolderDragKey(s.key)) }}
      onDragEnd={() => { setFolderDragKey(null); setOverFolder(null) }}
      className="group relative flex cursor-grab active:cursor-grabbing select-none items-center gap-3 rounded-xl bg-surface-container px-4 py-3 transition-colors hover:bg-surface-high">
      <GripVertical size={13} className="pointer-events-none absolute left-0.5 top-1/2 -translate-y-1/2 text-on-surface-low opacity-0 group-hover:opacity-100 transition-opacity" />
      {
}
      <Checkbox checked={selected.has(s.key)} onChange={() => toggleSelected(s.key)}
        ariaLabel={`Select ${sessionTitle(s)}`}
        className={`transition-opacity ${selecting ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100'}`} />
      <span className="grid size-9 shrink-0 place-items-center rounded-lg" style={{ background: 'color-mix(in srgb, var(--color-primary) 14%, transparent)' }}>
        <MessageSquare size={17} className="text-primary" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-on-surface text-[0.9375rem]" style={fvs(500)}>{sessionTitle(s)}</div>
        {
}
        {contentSnippets.get(s.key) && (
          <div className="mt-0.5 truncate text-on-surface-var text-[0.8125rem]">
            {snippetParts(contentSnippets.get(s.key) as string).map((part, i) => (
              part.hit
                ? <mark key={i} className="rounded bg-primary/25 px-0.5 text-on-surface">{part.text}</mark>
                : <span key={i}>{part.text}</span>
            ))}
          </div>
        )}
        <div className="flex items-center gap-1.5 flex-wrap text-on-surface-low text-[0.8125rem]">
          <span>{s.messages} message{s.messages === 1 ? '' : 's'}{s.running ? ' · running' : ''}{s.model ? ` · ${s.model}` : ''}</span>
          {
}
          {s.origin && s.origin !== 'manual' && (() => {
            const kind = s.origin === 'code' ? 'code project' : s.origin === 'loop' ? 'loop' : s.origin === 'channel' ? 'channel' : 'campaign'
            const label = s.source_label || s.source_id || kind
            const canOpen = !!s.source_id && (s.origin === 'code' || s.origin === 'loop')
            const chip = 'inline-flex items-center gap-1 rounded-pill px-1.5 h-[18px] text-[0.75rem] transition-colors'
            const tint = { background: 'color-mix(in srgb, var(--color-secondary) 18%, transparent)', color: 'var(--color-secondary)' }
            const glyph = s.origin === 'code' ? <CodeIcon size={10} /> : <Target size={10} />
            if (!canOpen) {
              return (
                <span title={`From a ${kind}`} className={`${chip} cursor-default`} style={tint}>
                  {glyph}
                  {label}
                </span>
              )
            }
            return (
              <button type="button"
                onClick={(e) => { e.stopPropagation(); navigate(`${s.origin === 'code' ? 'code' : 'loops'}/${s.source_id}`) }}
                title={`From ${kind} “${label}” — open its cockpit`}
                className={`${chip} hover:brightness-125 cursor-pointer`}
                style={tint}>
                {glyph}
                {label}
              </button>
            )
          })()}
          {(s.tags ?? []).map((tid) => tagById[tid] && (
            <span key={tid} className="inline-flex items-center rounded-pill px-1.5 h-[18px] text-[0.75rem]"
              style={{ background: `color-mix(in srgb, ${tagById[tid].color || 'var(--color-primary)'} 18%, transparent)`, color: tagById[tid].color || 'var(--color-primary)' }}>{tagById[tid].name}</span>
          ))}
        </div>
      </div>
      { }
      <SessionOrgMenu orgLoadFailed={!!foldersError || !!tagsError} s={s} folders={folders} tags={tags} onSetFolder={setFolder} onToggleTag={toggleTag} />
      <SquareIconButton label={s.pinned ? 'Unpin chat' : 'Pin chat'} title={s.pinned ? 'Unpin' : 'Pin to top'} on={s.pinned}
        onClick={(e) => { e.stopPropagation(); togglePin(s.key, !s.pinned) }}
        className={`shrink-0 transition-opacity ${s.pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`}>
        <Pin size={14} className={s.pinned ? 'fill-current' : ''} />
      </SquareIconButton>
      <IconButton icon={Trash2} label="Delete chat" onClick={(e) => { e.stopPropagation(); del(s) }} size={26} iconSize={14}
        tone="danger"
        className="shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100" />
    </div>
    </ContextMenu>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar
        keepCornerPadding
        left={<span data-type="title-l" className="text-on-surface">Chat history</span>}
        right={<HeaderActions className="max-w-[60vw]">
          <HeaderSegmented ariaLabel="View" value={view}
            options={[{ key: 'list', label: 'List view', icon: ListIcon }, { key: 'board', label: 'Board view (by tag)', icon: Columns3 }]}
            onChange={(v) => setView(v as 'list' | 'board')} />
          {
}
          <HeaderControl
            icon={retagRunning ? Loader2 : Sparkles}
            label={retagRunning ? `Generating ${retag?.done ?? 0}/${retag?.total ?? '…'} — click to cancel` : 'Generate Tags'}
            active={retagRunning} priority="default" onClick={startRetag}
            className={retagRunning ? '[&>svg]:animate-spin' : undefined} />
          {
}
          <HeaderControl icon={FolderPlus} label="New folder" priority="low" onClick={createFolder} />
          <HeaderControl icon={Edit3} label="New chat" variant="primary" priority="primary" onClick={() => navigate('chat/new')} />
        </HeaderActions>} />
      {
}
      <div className="flex min-h-0 flex-1">
      {
}
      <div className="flex min-w-0 flex-1 min-h-0 flex-col">
        <div className="mx-auto w-full px-l pt-l shrink-0" style={{ maxWidth: 'var(--content-width)' }}>
          {
}
          {(sessions === null || sessions.length > 0) && (<>
            {
}
            {(originCounts.loop > 0 || originCounts.code > 0 || originCounts.channel > 0) && (
              <div className="mb-m">
                <Segmented ariaLabel="Chat origin" value={origin} onChange={(v) => setOrigin(v as typeof origin)}
                  options={[
                    { key: 'manual', label: `Chat Sessions${originCounts.manual ? ` ${originCounts.manual}` : ''}` },
                    ...(originCounts.loop > 0 ? [{ key: 'loop', label: `Loops ${originCounts.loop}` }] : []),
                    ...(originCounts.code > 0 ? [{ key: 'code', label: `Code ${originCounts.code}` }] : []),
                    ...(originCounts.channel > 0 ? [{ key: 'channel', label: `Channels ${originCounts.channel}` }] : []),
                    { key: 'all', label: 'All' },
                  ]} />
              </div>
            )}
            <div className="mb-m">
              <SearchField value={q} onChange={setQ} placeholder="Search chats — title or anything said"
                ariaLabel="Search chats" autoFocus />
              {
}
              <ResultAnnouncement count={filtered.length} noun="chats"
                active={!!n || origin !== 'manual'} />
            </div>
            {
}
            <div className="mb-m flex items-center gap-2">
              <Segmented ariaLabel="Chat lifecycle" value={showArchived ? 'archived' : 'active'}
                onChange={(v) => { clearSelection(); setShowArchived(v === 'archived') }}
                options={[{ key: 'active', label: 'Active' }, { key: 'archived', label: 'Archived' }]} />
              {showArchived && (
                <span className="text-on-surface-low text-[0.75rem]">
                  Archived chats stay searchable — restore any of them at any time.
                </span>
              )}
            </div>
            {
}
            {selecting && (
              <div className="mb-m flex flex-wrap items-center gap-2 rounded-lg bg-surface-low px-m py-2 ring-1 ring-outline-variant/40">
                <span data-type="label-l" className="text-on-surface">{selected.size} selected</span>
                {showArchived ? (
                  <Button variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON} onClick={() => runBulk('restore')}>
                    <ArchiveRestore size={13} /> Restore
                  </Button>
                ) : (
                  <Button variant="tonal" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON} onClick={() => runBulk('archive')}>
                    <Archive size={13} /> Archive
                  </Button>
                )}
                <Button variant="ghost" size="xs" disabled={bulkBusy} disabledReason={BUSY_REASON}
                  onClick={() => runBulk('never_archive', { value: true })}
                  title="Exempt these chats from auto-archive">
                  <Pin size={13} /> Never archive
                </Button>
                <Button variant="ghost" size="xs" onClick={clearSelection}>Clear</Button>
              </div>
            )}
            {
}
            {bulkNote && !selecting && (
              <div role="status" className="mb-m text-on-surface-var text-[0.8125rem]">{bulkNote}</div>
            )}
            {tags.length > 0 && (
              <div role="group" aria-label="Filter by tag" className="mb-m flex flex-wrap items-center gap-1.5">
                <span className="text-on-surface-low text-[0.75rem] mr-1">Filter:</span>
                {tags.map((t) => {
                  const on = tagFilter.has(t.id)
                  return (
                    <button key={t.id} type="button" aria-pressed={on} onClick={() => { const nx = new Set(tagFilter); nx.has(t.id) ? nx.delete(t.id) : nx.add(t.id); setTagFilter(nx) }}
                      className="inline-flex items-center gap-1 rounded-pill px-2 h-7 text-[0.75rem] transition-colors"
                      style={on ? { background: `color-mix(in srgb, ${t.color || 'var(--color-primary)'} 22%, transparent)`, color: t.color || 'var(--color-primary)' } : { background: 'var(--color-surface-high)', color: 'var(--color-on-surface-var)' }}>
                      <TagIcon size={11} /> {t.name}
                    </button>
                  )
                })}
                {tagFilter.size > 0 && <Button variant="ghost" size="xs" onClick={() => setTagFilter(new Set())} className="h-6 px-1 text-[0.75rem] text-on-surface-low">Clear</Button>}
              </div>
            )}
          </>)}
        </div>

        {sessions === null && sessionsError ? <div className="flex-1 min-h-0"><LoadError what="chats" error={sessionsError} onRetry={refreshSessions} /></div>
          : sessions === null ? <div className="flex-1 min-h-0"><ListSkeleton rows={6} what="chats" /></div>
          : sessions.length === 0 ? <div className="flex-1 min-h-0"><EmptyState icon={MessageSquare} title="No chats yet" hint="Start a conversation — your sessions will appear here to search and revisit." action={{ label: 'New chat', onClick: () => navigate('chat/new'), icon: Edit3 }} /></div>
          : filtered.length === 0 ? <div className="flex-1 min-h-0">{
              n ? (
                <EmptyState icon={Search} title={`No chats match “${q.trim()}”`}
                  hint={`You have ${sessions.length} chat${sessions.length === 1 ? '' : 's'} — just none matching the search.`}
                  action={{ label: 'Clear search', onClick: () => setQ('') }} />
              ) : (
                <EmptyState icon={Filter} title="No chats in this view"
                  hint={`You have ${sessions.length} chat${sessions.length === 1 ? '' : 's'} — just none in this view.`}
                  action={{ label: 'View all chats', onClick: () => { setTagFilter(new Set()); setOrigin('all') } }} />
              )
            }</div>
          : view === 'board' ? (
            <div className="flex-1 min-h-0 px-l pb-l">
              <div className="mx-auto h-full w-full" style={{ maxWidth: 'var(--content-width)' }}>
                <TagBoard sessions={filtered} tags={tags} card={card} onMove={setColumnTag} />
              </div>
            </div>
          )
          : (
            <div className="flex-1 min-h-0 overflow-y-auto">
              <div className="mx-auto w-full px-l pb-l flex flex-col gap-l" style={{ maxWidth: 'var(--content-width)' }}>
                {byFolder.map((g) => {
                  const dropId = g.folder?.id ?? ''
                  const isOver = folderDragKey != null && overFolder === dropId
                  return (
                  <div key={g.folder?.id ?? '_ungrouped'}
                    onDragOver={folderDragKey ? (e) => { e.preventDefault(); if (overFolder !== dropId) setOverFolder(dropId) } : undefined}
                    onDragLeave={folderDragKey ? (e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOverFolder((c) => (c === dropId ? null : c)) } : undefined}
                    onDrop={folderDragKey ? (e) => { e.preventDefault(); const k = e.dataTransfer.getData('text/plain') || folderDragKey; if (k) setFolder(k, g.folder?.id ?? null); setOverFolder(null); setFolderDragKey(null) } : undefined}
                    className={`rounded-xl transition-colors ${isOver ? 'bg-primary/10 outline-2 outline-dashed outline-primary/50' : ''}`}>
                    {g.folder && (
                      <div className="mb-2 flex items-center gap-1.5 text-on-surface-var text-[0.8125rem]" style={fvs(500)}>
                        <Folder size={14} /> {g.folder.name}
                        <span className="text-on-surface-low">({g.items.length})</span>
                      </div>
                    )}
                    {g.items.length === 0 ? <div className="text-on-surface-low text-[0.8125rem] italic pl-5">{folderDragKey ? 'Drop here to move into this folder' : 'Empty'}</div>
                      : (
                        <WindowedList
                          items={g.items}
                          rowKey={(s) => s.key}
                          rowHeights="variable"
                          estimateRowHeight={66}
                          gap={8}
                          noun="chats"
                          findHint="use the Search chats field above, which searches every chat including their contents."
                          anchorKey={peekKey || undefined}
                          className="flex flex-col gap-s"
                        >
                          {(s) => card(s)}
                        </WindowedList>
                      )}
                  </div>
                  )
                })}
              </div>
            </div>
          )}
      </div>
      {
}
      <AnimatePresence>
        {peekKey && (
          <SidePanel key={peekKey} title={peekSession ? sessionTitle(peekSession) : peekKey} icon={<MessageSquare size={18} className="text-primary" />}
            storeKey="chat-peek-w" fillHeight urlKey={{ key: 'peek', setQuery }}
            onExpand={() => navigate(`chat/${peekKey}`)}
            onClose={() => setPeekKey('')}>
            <SessionPeekBody sessionKey={peekKey} onOpen={() => navigate(`chat/${peekKey}`)} />
          </SidePanel>
        )}
      </AnimatePresence>
      </div>
    </div>
  )
}

function SessionOrgMenu({ s, folders, tags, orgLoadFailed, onSetFolder, onToggleTag }: {
  s: ChatSessionSummary; folders: ChatFolder[]; tags: ChatTag[]
  orgLoadFailed?: boolean
  onSetFolder: (key: string, folderId: string | null) => void; onToggleTag: (key: string, tagId: string) => void
}) {
  return (
    <div onClick={(e) => e.stopPropagation()}>
      {
}
      {
}
      <Popover width={240} align="right" placement="bottom" portal trigger={(open, toggle) => (
        <SquareIconButton icon={TagIcon} label="Organize chat" title="Folder & tags" ariaExpanded={open} onClick={toggle}
          className={`shrink-0 transition-opacity ${open ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}`} />
      )}>
        {() => (
          <div className="max-h-[320px] overflow-y-auto py-1">
            {folders.length > 0 && <div className="px-m pt-1 pb-0.5 text-[0.75rem] uppercase tracking-wide text-on-surface-low">Folder</div>}
            {folders.length > 0 && (
              <MenuRow label="— none —" selected={!s.folder_id} onClick={() => onSetFolder(s.key, null)} />
            )}
            {folders.map((f) => <MenuRow key={f.id} label={f.name} icon={<Folder size={14} />} selected={s.folder_id === f.id} onClick={() => onSetFolder(s.key, f.id)} />)}
            {tags.length > 0 && <div className="px-m pt-2 pb-0.5 text-[0.75rem] uppercase tracking-wide text-on-surface-low">Tags</div>}
            {tags.map((t) => (
              <MenuRow key={t.id} label={t.name} selected={(s.tags ?? []).includes(t.id)} onClick={() => onToggleTag(s.key, t.id)} />
            ))}
            { }
            {orgLoadFailed && folders.length === 0 && tags.length === 0
              ? <div className="px-m py-2"><FieldError>Couldn't load your folders and tags</FieldError></div>
              : folders.length === 0 && tags.length === 0 && <div className="px-m py-2 text-[0.8125rem] text-on-surface-low">Create a folder or tag first.</div>}
          </div>
        )}
      </Popover>
    </div>
  )
}


function TagBoard({ sessions, tags, card, onMove }: {
  sessions: ChatSessionSummary[]; tags: ChatTag[]; card: (s: ChatSessionSummary) => React.ReactNode
  onMove?: (key: string, toTagId: string | null, fromTagId: string | null) => void
}) {
  const [dragKey, setDragKey] = useState<string | null>(null)
  const dragFromCol = useRef<string | null>(null)
  const [overCol, setOverCol] = useState<string | null>(null)
  const collapse = useBoardCollapse('board-collapsed:chat')
  const columns: { id: string; tagId: string | null; label: string; color?: string; items: ChatSessionSummary[] }[] = [
    ...tags.map((t) => ({ id: t.id, tagId: t.id, label: t.name, color: t.color, items: sessions.filter((s) => (s.tags ?? []).includes(t.id)) })),
    { id: '_untagged', tagId: null, label: 'Untagged', items: sessions.filter((s) => !(s.tags ?? []).length) },
  ]
  const template = boardGridTemplate(columns.map((c) => collapse.isCollapsed(c.id, c.items.length)))
  return (
    <div
      className="grid h-full gap-m overflow-x-auto"
      style={{ gridTemplateColumns: template, gridAutoRows: 'minmax(180px, 1fr)' }}
    >
      {columns.map((c) => {
        const collapsed = collapse.isCollapsed(c.id, c.items.length)
        const dropStyle = {
          background: overCol === c.id
            ? 'color-mix(in srgb, var(--color-primary) 12%, transparent)'
            : 'color-mix(in srgb, var(--color-surface-container) 40%, transparent)',
          outline: overCol === c.id ? '1.5px dashed color-mix(in srgb, var(--color-primary) 60%, transparent)' : 'none',
        }
        const dropHandlers = onMove ? {
          onDragOver: (e: React.DragEvent) => { e.preventDefault(); if (overCol !== c.id) setOverCol(c.id) },
          onDragLeave: (e: React.DragEvent) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOverCol((p) => p === c.id ? null : p) },
          onDrop: (e: React.DragEvent) => { e.preventDefault(); const k = e.dataTransfer.getData('text/plain') || dragKey; if (k) onMove(k, c.tagId, dragFromCol.current); setOverCol(null); setDragKey(null); dragFromCol.current = null },
        } : {}
        if (collapsed) {
          return (
            <CollapsedBoardColumn key={c.id} icon={TagIcon} label={c.label} count={c.items.length}
              tone={c.color} style={dropStyle} {...dropHandlers}
              onExpand={() => collapse.toggle(c.id, c.items.length)} />
          )
        }
        return (
          <div key={c.id} className="flex min-h-0 flex-col rounded-xl p-2 transition-colors" style={dropStyle} {...dropHandlers}>
            <div className="mb-2 flex items-center gap-1.5 px-1 pt-1 shrink-0 text-[0.8125rem]" style={withWeight({ color: c.color || 'var(--color-on-surface)' }, 550)}>
              <TagIcon size={13} /> <span className="truncate flex-1">{c.label}</span> <span className="text-on-surface-low tabular-nums">{c.items.length}</span>
              <CollapseColumnButton onCollapse={() => collapse.toggle(c.id, c.items.length)} />
            </div>
            <div className="flex flex-1 min-h-0 flex-col gap-s overflow-y-auto pr-0.5">
              {c.items.length === 0
                ? <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-outline-variant/30 py-6 text-on-surface-low text-[0.75rem]">{onMove ? 'Drop a chat here' : 'No chats'}</div>
                : c.items.map((s) => onMove
                  ? <div key={s.key} draggable
                      onDragStart={(e) => { e.dataTransfer.setData('text/plain', s.key); e.dataTransfer.effectAllowed = 'move'; dragFromCol.current = c.tagId; requestAnimationFrame(() => setDragKey(s.key)) }}
                      onDragEnd={() => { setDragKey(null); setOverCol(null); dragFromCol.current = null }}
                      className={`select-none cursor-grab active:cursor-grabbing ${dragKey === s.key ? 'opacity-40' : ''}`}>{card(s)}</div>
                  : card(s))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

function AutoNudgeMenuItem({ session, onOpen }: { session: string; onOpen: () => void }) {
  const [open, setOpen] = useState(false)
  const [enabled, setEnabled] = useState(true)
  const [loop, setLoop] = useState<NudgeLoop | null>(null)
  const [msg, setMsg] = useState('')
  const [idle, setIdle] = useState(60)
  const [maxCycles, setMaxCycles] = useState(0)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    api.autonudgeGet(session).then((r) => {
      setEnabled(r.enabled)
      setLoop(r.loop)
      if (r.loop) { setMsg(r.loop.message); setIdle(r.loop.idle_secs); setMaxCycles(r.loop.max_cycles) }
    }).catch(() => setEnabled(false))
  }, [session])
  useEffect(load, [load])

  async function arm() {
    if (!msg.trim() || busy) return
    setBusy(true)
    try {
      if (loop) await api.autonudgeUpdate(loop.id, { message: msg.trim(), idle_secs: idle, max_cycles: maxCycles })
      else await api.autonudgeStart({ session_name: session, message: msg.trim(), idle_secs: idle, max_cycles: maxCycles })
      load(); setOpen(false)
    } finally { setBusy(false) }
  }
  async function stop() {
    if (!loop || busy) return
    setBusy(true)
    try { await api.autonudgeDelete(loop.id); setLoop(null) } finally { setBusy(false) }
  }

  return (
    <>
      <MenuRow icon={<Repeat size={16} />} label="Auto-nudge" hint={loop?.active ? 'On — keeps this chat working when idle' : 'Keep this chat working when idle'}
        onClick={() => { onOpen(); load(); setOpen(true) }} />
      {open && (
        <Modal title="Auto-nudge" icon={<Repeat size={18} className="text-primary" />} onClose={() => setOpen(false)}>
          <div className="flex flex-col gap-2">
            {!enabled ? (
              <p className="text-[0.8125rem] text-on-surface-low">Disabled on this server (<code className="font-mono">GIDEON_AUTONUDGE=0</code>).</p>
            ) : (<>
              <p className="text-[0.8125rem] text-on-surface-low">When a turn finishes and you're idle, this message is re-injected into this chat to keep it working on its own.</p>
              <textarea value={msg} onChange={(e) => setMsg(e.target.value)} rows={3} autoFocus
                placeholder="e.g. Continue toward the goal; if done, write a summary and stop."
                className="w-full rounded-md bg-surface-high px-2 py-1.5 text-on-surface text-[0.8125rem] outline-none resize-y focus:ring-2 focus:ring-inset focus:ring-primary" />
              <div className="flex items-center gap-3 text-[0.8125rem] text-on-surface-var">
                <label className="flex items-center gap-1">Idle
                  <input type="number" min={15} value={idle} onChange={(e) => setIdle(Number(e.target.value))} className="w-16 rounded bg-surface-high px-1.5 py-0.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" />s</label>
                <label className="flex items-center gap-1">Max cycles
                  <input type="number" min={0} value={maxCycles} onChange={(e) => setMaxCycles(Number(e.target.value))} className="w-14 rounded bg-surface-high px-1.5 py-0.5 text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" /></label>
              </div>
              {loop && <p className="text-[0.75rem] text-on-surface-low">Active · {loop.cycle_count} cycle{loop.cycle_count === 1 ? '' : 's'} fired{loop.max_cycles ? ` / ${loop.max_cycles}` : ''}.</p>}
              <div className="flex justify-end gap-2 mt-1">
                {loop && <Button variant="ghost" size="sm" onClick={stop} disabled={busy} disabledReason={BUSY_REASON}><X size={14} /> Stop</Button>}
                <Button size="sm" onClick={arm} disabled={busy || !msg.trim()}
                  disabledReason={!msg.trim() ? 'Write the message first' : BUSY_REASON}><Check size={14} /> {loop ? 'Update' : 'Arm'}</Button>
              </div>
            </>)}
          </div>
        </Modal>
      )}
    </>
  )
}
