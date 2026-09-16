import { createElement, forwardRef, lazy, Suspense, useId, useImperativeHandle, useRef, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { Save, RotateCcw, Eye, Code2, Columns2, WrapText, Copy, Check, Loader2, FileWarning, Download, type LucideIcon } from 'lucide-react'
import { spring } from '../../theme/motion'
import { SquareIconButton } from '../SquareIconButton'
import { Centered } from '../Centered'
import { useMode } from '../../../app/shell/theme'
import { CommentLayer } from '../../../features/files/comments/CommentLayer'
import type { CommentTarget } from './commentTarget'
import { type ContentType, isCommentable } from './contentTypes'
import type { IterationTarget } from '../widget/useArtifactIteration'
import { contentPermissions, useContentDraft, useContentScroll, useContentTools } from './contentSurfaceState'

const MonacoEditor = lazy(() => import('@monaco-editor/react'))

export interface ContentSurfaceHandle { save: () => void }

export interface ContentAction {
  icon: LucideIcon
  label: string
  title?: string
  primary?: boolean
  run: (draft: string) => void | Promise<void>
}

interface ContentSurfaceProps {
  type: ContentType
  content: string
  title: string
  docId: string
  path?: string
  readOnly?: boolean
  onSave?: (draft: string) => void | Promise<void>
  commentTarget?: CommentTarget
  compact?: boolean
  initialView?: 'preview' | 'edit' | 'split'
  draftStore?: Map<string, { draft: string; base: string; warned?: boolean }>
  truncated?: boolean
  actions?: ContentAction[]
  onDirtyChange?: (dirty: boolean) => void
  onDraftChange?: (draft: string, dirty: boolean) => void
  confirmSave?: () => boolean | Promise<boolean>
  language?: string
  headerLeft?: ReactNode
  headerExtras?: ReactNode
  banner?: ReactNode
  iterate?: IterationTarget
}

export const ContentSurface = forwardRef<ContentSurfaceHandle, ContentSurfaceProps>(function ContentSurface(props, ref) {
  const { type, content, title, docId, path, readOnly, onSave, commentTarget, compact = false, initialView, draftStore, truncated, actions, onDirtyChange, onDraftChange, confirmSave, language, headerLeft, headerExtras, banner, iterate } = props
  const { mode } = useMode()
  const capability = contentPermissions(type, readOnly, truncated, onSave)
  const state = useContentDraft({ id: docId, content, previewable: capability.previewable, editable: capability.draftEditable, initialView, cache: draftStore, save: onSave, confirm: confirmSave, onDirty: onDirtyChange, onDraft: onDraftChange })
  const { draft, view, dirty, saving } = state
  const tools = useContentTools(draft)
  const scroll = useContentScroll()
  const previewScrollRef = useRef<HTMLDivElement>(null)
  const splitPreviewRef = scroll.preview
  const indicator = `content-view-${useId()}`
  useImperativeHandle(ref, () => ({ save: state.save }))
  const exports = type.exports ?? []
  const showToolbar = capability.editable || capability.previewable || !!headerLeft || !!headerExtras || !!exports.length
  const preview = () => type.preview ? createElement(type.preview.render, { content: draft, title, mode, path, streaming: false, iterate }) : null
  const editor = (split = false) => {
    if (capability.custom && type.edit?.render) return createElement(type.edit.render, { slug: docId, title, mode, readOnly, onDirty: state.setCustomDirty })
    return <Suspense fallback={<Centered><Loader2 size={18} className="animate-spin text-on-surface-low" /></Centered>}>
      <MonacoEditor height="100%" path={path} language={language || type.edit?.language || 'plaintext'} value={draft} onChange={value => state.setDraft(value ?? '')}
        theme={mode === 'light' ? 'light' : 'vs-dark'} onMount={split ? scroll.mount : undefined}
        options={{ ariaLabel: `${title} — editor`, readOnly: !capability.draftEditable, fontSize: 13, minimap: { enabled: view !== 'split' }, scrollBeyondLastLine: false,
          wordWrap: tools.wrap ? 'on' : 'off', lineNumbers: 'on', automaticLayout: true, padding: { top: 10, bottom: 10 }, tabSize: 2, renderWhitespace: 'selection' }} />
    </Suspense>
  }
  const views = [{ id: 'preview' as const, label: 'Preview', icon: Eye }, ...(capability.splittable ? [{ id: 'split' as const, label: 'Split', icon: Columns2 }] : []), { id: 'edit' as const, label: 'Edit', icon: Code2 }]
  return <div className="flex h-full flex-col">
    {showToolbar && <div className="flex flex-wrap items-center gap-s border-b border-outline/40 px-m py-1.5">
      {headerLeft}
      {truncated && <span className="rounded-md bg-surface-high px-2 py-1 text-xs text-on-surface-low" title="Only the first part of this large file was loaded — read-only so a save can't truncate the rest.">truncated · read-only</span>}
      {state.anyDirty && <span className="size-1.5 shrink-0 rounded-full bg-primary" title="Unsaved changes" />}
      <div className="ml-auto flex flex-wrap items-center justify-end gap-1">
        {capability.editable && capability.previewable && <div className="mr-1 inline-flex rounded-lg border border-outline-variant/40 bg-surface-container p-0.5" role="group" aria-label="Document view">
          {views.map(item => <ToggleBtn key={item.id} icon={item.icon} label={item.label} on={view === item.id} onClick={() => state.setView(item.id)} compact={compact} indicatorId={indicator} />)}
        </div>}
        {view !== 'preview' && capability.draftEditable && <>
          <SquareIconButton icon={WrapText} label="Toggle word wrap" on={tools.wrap} iconSize={13} onClick={() => tools.setWrap(value => !value)} />
          <SquareIconButton icon={tools.copied ? Check : Copy} label="Copy contents" iconSize={13} onClick={tools.copy} />
        </>}
        {headerExtras}
        {!!exports.length && <div className="relative">
          <SquareIconButton icon={Download} label="Export" iconSize={13} ariaExpanded={tools.exportOpen} onClick={() => tools.setExportOpen(open => !open)} />
          {tools.exportOpen && <>
            <div className="fixed inset-0 z-40" onClick={() => tools.setExportOpen(false)} />
            <div className="absolute right-0 z-50 mt-1 min-w-40 overflow-hidden rounded-lg border border-outline-variant/50 bg-surface p-1 shadow-lg">
              {exports.map(target => <button key={target.id} type="button" onClick={() => { tools.setExportOpen(false); void target.run(draft, title) }} className="block w-full rounded px-3 py-2 text-left text-xs text-on-surface hover:bg-surface-high">{target.label}</button>)}
            </div>
          </>}
        </div>}
        {capability.draftEditable && <>
          <SquareIconButton icon={RotateCcw} label="Revert unsaved changes" disabled={!dirty} iconSize={13} onClick={() => state.setDraft(content)} />
          <button type="button" onClick={dirty ? state.save : undefined} disabled={saving} aria-busy={saving || undefined} aria-disabled={(!dirty && !saving) || undefined}
            className="inline-flex h-7 items-center gap-1 rounded-md px-2.5 text-xs disabled:opacity-40 aria-disabled:opacity-40"
            style={{ background: dirty ? 'var(--color-primary)' : 'var(--color-surface-high)', color: dirty ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }} title={dirty ? 'Save (⌘S)' : 'Save (⌘S) — no changes to save'}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}{!compact && 'Save'}
          </button>
          {actions?.map(action => <button key={action.label} type="button" onClick={() => state.action(action.run)} disabled={saving} aria-busy={saving || undefined}
            className="inline-flex h-7 items-center gap-1 rounded-md px-2.5 text-xs disabled:opacity-40"
            style={action.primary ? { background: 'var(--color-primary)', color: 'var(--color-on-primary)' } : { color: 'var(--color-on-surface-low)' }} title={action.title || action.label}>
            {saving ? <Loader2 size={13} className="animate-spin" /> : <action.icon size={13} />}{!compact && action.label}
          </button>)}
        </>}
      </div>
    </div>}
    {banner}
    <div className="min-h-0 flex-1 overflow-hidden">
      {view === 'preview' && capability.previewable ? <div ref={previewScrollRef} tabIndex={0} role="group" aria-label={`${title || 'Document'} preview`} className="relative h-full overflow-auto">
        {preview()}
        {!!commentTarget && isCommentable(type) && <CommentLayer scrollRef={previewScrollRef} docId={docId} docLabel={title} docPath={path} content={draft} onSubmit={(message, docPaths) => commentTarget.submit({ message, docPaths })} />}
      </div> : view === 'split' && capability.previewable ? <div className="grid h-full grid-cols-1 lg:grid-cols-2">
        <div className="min-h-0 border-b border-outline/40 lg:border-b-0 lg:border-r">{editor(true)}</div>
        <div ref={splitPreviewRef} onScroll={scroll.fromPreview} tabIndex={0} role="group" aria-label={`${title || 'Document'} preview`} className="min-h-0 overflow-auto">{preview()}</div>
      </div> : <div className="h-full">{editor()}</div>}
    </div>
  </div>
})

function ToggleBtn({ icon: Icon, label, on, onClick, compact, indicatorId }: { icon: LucideIcon; label: string; on: boolean; onClick: () => void; compact: boolean; indicatorId: string }) {
  return <button type="button" aria-label={label} aria-pressed={on} title={compact ? label : undefined} onClick={onClick}
    className={`relative inline-flex h-6 items-center gap-1 rounded-md ${compact ? 'px-2' : 'px-2.5'} ${on ? 'text-on-surface' : 'text-on-surface-low'}`}>
    {on && <motion.span layoutId={indicatorId} transition={spring.spatialFast} className="absolute inset-0 rounded-md bg-surface-highest" />}
    <Icon size={12} className="relative" />{!compact && <span className="relative text-xs">{label}</span>}
  </button>
}
export { FileWarning }
