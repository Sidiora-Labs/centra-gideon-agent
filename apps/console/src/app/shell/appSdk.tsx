
import { availableModules, installModuleCatalog, moduleFacade, resolveModuleSource, type HostModule } from '../extensions/modules'
import * as React from 'react'
import * as ReactDOM from 'react-dom'
import * as ReactDOMClient from 'react-dom/client'
import { useEffect, useRef, useState, createContext, useContext, createElement } from 'react'
import {
  AlertTriangle, ArrowLeft, ArrowRight, BookOpen, Calendar, Check, CheckCircle2,
  ChevronDown, ChevronRight, Clock, Download, ExternalLink, Eye, FileText,
  FolderKanban, GitBranch, Inbox, Lightbulb, Link2, ListChecks, Loader2, Lock,
  MessageSquare, Mic, NotebookPen, Pencil, Play, Plus, Presentation, Puzzle,
  RefreshCw, RotateCcw, Search, Send, ShieldCheck, Sparkles, SquareCheck, Star,
  Target, Trash2, Users, Video, X, Zap,
} from 'lucide-react'
import { useInvestigate } from '../../shared/data/investigate'
import { Button } from '../../shared/ui/Button'
import { Surface } from '../../shared/ui/Surface'
import { GenUiWidget } from '../../shared/ui/genui/GenUiWidget'
import {
  registerLayerComponent,
  removeComponentsFrom,
  type GenUiComponentDef,
  type GenUiRegisterResult,
} from '../../shared/ui/genui/registry'
import { LAYER_APP } from '../../shared/ui/surfaces/layers'

export interface AppPermissions {
  api?: string[]
  events?: string[]
  mcpTools?: string[]
  storage?: boolean
  network?: boolean
  memory?: string
  cron?: boolean
}

export type UiCapability = 'shell-primitives' | 'generative-widget' | 'generative-component'

export interface AppContext {
  name: string
  permissions: AppPermissions
  uiCapabilities?: string[]
  host?: {
    setHeaderActions: (actions: Array<{ id: string; label: string; icon?: string; variant?: 'primary' | 'secondary' | 'ghost'; onClick: () => void }>) => void
    openPanel: (spec: { title: string; icon?: string; render: (el: HTMLElement) => void | (() => void) }) => void
    closePanel: () => void
  }
}

const AppCtx = createContext<AppContext>({ name: '', permissions: {} })

export function AppApiProvider({ app, children }: { app: AppContext; children: React.ReactNode }) {
  return <AppCtx.Provider value={app}>{children}</AppCtx.Provider>
}

function matchesAny(value: string, patterns: string[] | undefined): boolean {
  if (!patterns) return false
  for (const pat of patterns) {
    if (pat === '*') return true
    if (pat.endsWith('*')) { if (value.startsWith(pat.slice(0, -1))) return true }
    else if (value === pat || value.startsWith(pat.replace(/\/$/, '') + '/')) return true
  }
  return false
}

export class AppPermissionError extends Error {
  constructor(message: string) { super(message); this.name = 'AppPermissionError' }
}

const SK = { 'X-Session-Key': 'dashboard:ui' }

const _appTokens = new Map<string, { token: string; exp: number }>()

async function appToken(appName: string): Promise<string> {
  const cached = _appTokens.get(appName)
  const now = Date.now() / 1000
  if (cached && cached.exp - 30 > now) return cached.token
  try {
    const r = await fetch(`/api/apps/${encodeURIComponent(appName)}/token`, { method: 'POST', headers: { ...SK } })
    if (!r.ok) return ''
    const { token, expires_in } = await r.json()
    _appTokens.set(appName, { token, exp: now + (Number(expires_in) || 3600) })
    return token || ''
  } catch { return '' }
}

async function appAuthHeaders(appName: string): Promise<Record<string, string>> {
  const t = await appToken(appName)
  return t ? { ...SK, Authorization: `Bearer ${t}` } : { ...SK }
}

export interface AppApiClient {
  backendBase: string
  get: <T>(path: string) => Promise<T>
  post: <T>(path: string, body?: unknown) => Promise<T>
  put: <T>(path: string, body?: unknown) => Promise<T>
  patch: <T>(path: string, body?: unknown) => Promise<T>
  del: <T>(path: string) => Promise<T>
  can: (path: string) => boolean
}

export function createAppApi(app: AppContext): AppApiClient {
  function allowed(path: string): boolean {
    const pathname = path.split(/[?#]/, 1)[0]
    if (pathname.startsWith(`/apps/${app.name}/api`)) return true
    return matchesAny(pathname, app.permissions.api)
  }

  async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
    if (!allowed(path)) {
      throw new AppPermissionError(
        `app "${app.name}" is not permitted to access ${path} — declare it in permissions.api`,
      )
    }
    const init: RequestInit = { method, headers: await appAuthHeaders(app.name) }
    if (body !== undefined) {
      init.headers = { ...init.headers, 'Content-Type': 'application/json' }
      init.body = JSON.stringify(body)
    }
    const r = await fetch(path, init)
    if (!r.ok) {
      const text = await r.text().catch(() => '')
      let msg = text || `HTTP ${r.status}`
      try { const p = JSON.parse(text); if (p?.error) msg = p.error } catch {   }
      throw new Error(msg)
    }
    const ct = r.headers.get('Content-Type') || ''
    return (ct.includes('application/json') ? await r.json() : await r.text()) as T
  }

  return {
    backendBase: `/apps/${app.name}/api`,
    get: <T,>(path: string) => request<T>('GET', path),
    post: <T,>(path: string, body?: unknown) => request<T>('POST', path, body),
    put: <T,>(path: string, body?: unknown) => request<T>('PUT', path, body),
    patch: <T,>(path: string, body?: unknown) => request<T>('PATCH', path, body),
    del: <T,>(path: string) => request<T>('DELETE', path),
    can: allowed,
  }
}

export function useAppApi(): AppApiClient {
  return createAppApi(useContext(AppCtx))
}

export interface AgentTaskResult {
  id: string
  done: boolean
  result?: string
  error?: string
  turns?: number
  elapsed?: number
}

export interface AgentTaskClient {
  start: (task: string, opts?: { agent?: string; maxTurns?: number }) => Promise<string>
  poll: (id: string) => Promise<AgentTaskResult>
  run: (task: string, opts?: { agent?: string; maxTurns?: number; signal?: AbortSignal }) => Promise<AgentTaskResult>
}

export function createAgentTask(appName: string): AgentTaskClient {
  async function start(task: string, opts?: { agent?: string; maxTurns?: number }): Promise<string> {
    const r = await fetch(`/api/apps/${encodeURIComponent(appName)}/agent-run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(await appAuthHeaders(appName)) },
      body: JSON.stringify({ task, agent: opts?.agent, max_turns: opts?.maxTurns }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error || `HTTP ${r.status}`)
    return (await r.json()).id as string
  }

  async function poll(id: string): Promise<AgentTaskResult> {
    const r = await fetch(`/api/apps/${encodeURIComponent(appName)}/agent-run/${encodeURIComponent(id)}`, { headers: await appAuthHeaders(appName) })
    if (!r.ok) throw new Error((await r.json().catch(() => ({})))?.error || `HTTP ${r.status}`)
    return await r.json() as AgentTaskResult
  }

  async function run(task: string, opts?: { agent?: string; maxTurns?: number; signal?: AbortSignal }): Promise<AgentTaskResult> {
    const id = await start(task, opts)
    for (;;) {
      if (opts?.signal?.aborted) throw new Error('aborted')
      const res = await poll(id)
      if (res.done) return res
      await new Promise((r) => setTimeout(r, 1500))
    }
  }

  return { start, poll, run }
}

export function useAgentTask(): AgentTaskClient {
  const app = useContext(AppCtx)
  return createAgentTask(app.name)
}

interface WsEnvelope { type: string; data: Record<string, unknown> }

export function createAppEvents(app: AppContext, onEvent: (e: WsEnvelope) => void): () => void {
  const events = app.permissions.events
  let ws: WebSocket | null = null
  let closed = false
  let timer: number | undefined
  let retry = 0
  const connect = async () => {
    if (closed) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const t = await appToken(app.name)
    if (closed) return
    const qs = t ? `?app_token=${encodeURIComponent(t)}` : ''
    ws = new WebSocket(`${proto}://${location.host}/api/ws${qs}`)
    ws.onmessage = (ev) => {
      try {
        const env = JSON.parse(ev.data) as WsEnvelope
        if (matchesAny(env.type, events)) onEvent(env)
      } catch {   }
    }
    ws.onclose = () => {
      if (closed) return
      retry = Math.min(retry + 1, 6)
      timer = window.setTimeout(connect, 250 * 2 ** retry)
    }
  }
  void connect()
  return () => { closed = true; if (timer) clearTimeout(timer); ws?.close() }
}

export function useAppEvents(onEvent: (e: WsEnvelope) => void) {
  const app = useContext(AppCtx)
  const cb = useRef(onEvent); cb.current = onEvent
  const events = app.permissions.events
  useEffect(() => createAppEvents(app, (e) => cb.current(e)), [events])  // eslint-disable-line react-hooks/exhaustive-deps
}

export interface AppTheme {
  mode: 'dark' | 'light'
  colors: {
    canvas: string; surface: string; surfaceHigh: string
    onSurface: string; onSurfaceLow: string; border: string
    primary: string; onPrimary: string
    ok: string; warn: string; danger: string
  }
  cssVars: Record<string, string>
}

const _APP_COLOR_TOKENS: Record<string, string> = {
  canvas: '--color-canvas',
  surface: '--color-surface',
  surfaceHigh: '--color-surface-high',
  onSurface: '--color-on-surface',
  onSurfaceLow: '--color-on-surface-low',
  border: '--color-outline-variant',
  primary: '--color-primary',
  onPrimary: '--color-on-primary',
  ok: '--color-ok',
  warn: '--color-warn',
  danger: '--color-danger',
}

function _readAppTheme(): AppTheme {
  const cs = getComputedStyle(document.documentElement)
  const mode: 'dark' | 'light' = document.documentElement.classList.contains('light') ? 'light' : 'dark'
  const colors = {} as AppTheme['colors']
  const cssVars: Record<string, string> = {}
  for (const [name, token] of Object.entries(_APP_COLOR_TOKENS)) {
    const val = cs.getPropertyValue(token).trim() || ''
    ;(colors as Record<string, string>)[name] = val
    cssVars[`--app-${name.replace(/[A-Z]/g, (m) => '-' + m.toLowerCase())}`] = `var(${token})`
  }
  return { mode, colors, cssVars }
}

export function readAppTheme(): AppTheme {
  return _readAppTheme()
}

export function useTheme(): AppTheme {
  const [theme, setTheme] = useState<AppTheme>(_readAppTheme)
  useEffect(() => {
    const obs = new MutationObserver(() => setTheme(_readAppTheme()))
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return theme
}

export function notify(message: string, level: 'info' | 'success' | 'error' = 'info'): void {
  try { window.dispatchEvent(new CustomEvent('ne:toast', { detail: { message, level } })) }
  catch {   }
}

export function useNotify() {
  return notify
}

export function setNavBadge(appName: string, count: number | null): void {
  try {
    window.dispatchEvent(new CustomEvent('ne:nav-badge', { detail: { app: appName, count } }))
  } catch {   }
}

export function useNavBadge() {
  const app = useContext(AppCtx)
  return (count: number | null) => setNavBadge(app.name, count)
}

export function launchChat(opts?: { agent?: string; prompt?: string; session?: string }): void {
  try {
    window.dispatchEvent(new CustomEvent('ne:launch-chat', { detail: opts ?? {} }))
  } catch {   }
}

export function useChatLauncher() {
  return launchChat
}

export function ChatEmbed(props: {
  session?: string
  prompt?: string
  agent?: string
  className?: string
  style?: React.CSSProperties
}) {
  const { session, prompt, agent, className, style } = props
  const qs = new URLSearchParams()
  if (prompt) qs.set('seed', prompt)
  if (agent) qs.set('agent', agent)
  qs.set('embed', '1')
  const q = qs.toString()
  const base = session ? `chat/${encodeURIComponent(session)}` : 'chat/new'
  const src = `${location.origin}/#/${base}?${q}`
  return createElement('iframe', {
    src,
    title: 'Gideon chat',
    className,
    style: { border: 'none', width: '100%', height: '100%', ...style },
    sandbox: 'allow-scripts allow-same-origin allow-forms',
  })
}


export function GenerativeWidget({ spec, title }: { spec: string; title?: string }) {
  return createElement(GenUiWidget, { content: spec, title: title ?? 'Widget' })
}

export function registerAppGenUiComponent(
  app: Pick<AppContext, 'name' | 'uiCapabilities'>,
  def: GenUiComponentDef,
): GenUiRegisterResult {
  if (!hasUiCapability(app, 'generative-component')) {
    return {
      ok: false,
      code: 'invalid',
      message: `${app.name || 'this app'} did not declare the generative-component UI capability.`,
    }
  }
  return registerLayerComponent(def, { layer: LAYER_APP, source: app.name })
}

export function unregisterAppGenUiComponents(appName: string): number {
  return removeComponentsFrom(appName)
}

export function hasUiCapability(
  app: Pick<AppContext, 'uiCapabilities'> | undefined,
  cap: UiCapability,
): boolean {
  return Boolean(app?.uiCapabilities?.includes(cap))
}

export function resolvableAppSpecs(app?: Pick<AppContext, 'uiCapabilities'>): string[] {
  return availableModules(HOST_MODULES, app?.uiCapabilities).map(({ specifier }) => specifier)
}

const APP_SDK_UI = { Button, Surface, useTheme, readAppTheme }

const APP_SDK_LUCIDE = {
  AlertTriangle, ArrowLeft, ArrowRight, BookOpen, Calendar, Check, CheckCircle2,
  ChevronDown, ChevronRight, Clock, Download, ExternalLink, Eye, FileText,
  FolderKanban, GitBranch, Inbox, Lightbulb, Link2, ListChecks, Loader2, Lock,
  MessageSquare, Mic, NotebookPen, Pencil, Play, Plus, Presentation, Puzzle,
  RefreshCw, RotateCcw, Search, Send, ShieldCheck, Sparkles, SquareCheck, Star,
  Target, Trash2, Users, Video, X, Zap,
}

const APP_SDK_GENUI = { GenerativeWidget, registerComponent: registerAppGenUiComponent, unregisterComponents: unregisterAppGenUiComponents }

const HOST_MODULES: readonly HostModule[] = [
  { specifier: 'react', exports: React },
  { specifier: 'react-dom', exports: ReactDOM },
  { specifier: 'react-dom/client', exports: ReactDOMClient },
  { specifier: 'lucide-react', exports: APP_SDK_LUCIDE },
  { specifier: '@gideon/app-sdk', exports: {
    AppApiProvider, AppPermissionError, useAppApi, useAppEvents, useTheme,
    useNotify, useNavBadge, setNavBadge, useChatLauncher, launchChat, useInvestigate,
    useAgentTask, createAgentTask, createAppApi, createAppEvents, notify, readAppTheme, ChatEmbed,
  } },
  { specifier: '@gideon/app-sdk/ui', exports: APP_SDK_UI, capability: 'shell-primitives' },
  { specifier: '@gideon/app-sdk/genui', exports: APP_SDK_GENUI, capability: 'generative-widget' },
]

type ModuleRegistry = Record<string, unknown>
const moduleHost = () => window as unknown as { __gideon_modules?: ModuleRegistry }
const moduleFacades = new Map<string, { exports: object; url: string }>()

export function installAppSdk(): void {
  const host = moduleHost()
  host.__gideon_modules ??= Object.create(null) as ModuleRegistry
  installModuleCatalog(HOST_MODULES, host.__gideon_modules)
}

export function appModuleShimUrl(specifier: string): string | null {
  const exports = moduleHost().__gideon_modules?.[specifier]
  if (!exports || typeof exports !== 'object') return null
  const existing = moduleFacades.get(specifier)
  if (existing?.exports === exports) return existing.url
  const url = URL.createObjectURL(new Blob([moduleFacade(specifier, exports)], { type: 'text/javascript' }))
  if (existing) URL.revokeObjectURL(existing.url)
  moduleFacades.set(specifier, { exports, url })
  return url
}

export async function loadContributedModule(
  src: string,
  app?: Pick<AppContext, 'uiCapabilities'>,
): Promise<Record<string, unknown>> {
  let source: string
  let sourceUrl = new URL(src, window.location.href).href
  try {
    const response = await fetch(src)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    source = await response.text()
    if (response.url) sourceUrl = response.url
  } catch {
    return import(/* @vite-ignore */ src) as Promise<Record<string, unknown>>
  }
  installAppSdk()
  const allowed = new Set(resolvableAppSpecs(app))
  const resolved = await resolveModuleSource(source, (specifier) =>
    allowed.has(specifier) ? appModuleShimUrl(specifier) : null, sourceUrl)
  const moduleUrl = URL.createObjectURL(new Blob([resolved], { type: 'text/javascript' }))
  try {
    return await import(/* @vite-ignore */ moduleUrl) as Record<string, unknown>
  } finally {
    URL.revokeObjectURL(moduleUrl)
  }
}
