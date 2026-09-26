import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ComposerPrimitive, unstable_useTriggerPopoverAriaProps, unstable_useTriggerPopoverTriggers, useAui, type Unstable_DirectiveFormatter, type Unstable_TriggerItem, type Unstable_TriggerPopoverAriaProps } from '@assistant-ui/react'
import { AnimatePresence, motion } from 'framer-motion'
import { Sparkles, Ear, Paperclip, MonitorUp, X } from 'lucide-react'
import { IconButton } from './IconButton'
import { physics, expr, useReducedMotion } from '../theme/motion'
import { AgentPill, ApprovalPill, ReasoningPill, NaturalVoicePill, effortsForAgent, PlusMenu } from './composer/controls'
import { modelChoices } from './composer/composerChoices'
import { Popover } from './Popover'
import { MarkdownInput, type MarkdownInputHandle } from './composer/MarkdownInput'
import { useMicRecorder } from './composer/useMicRecorder'
import { useComposerSurface } from './composer/useComposerSurface'
import { COMPOSER_HEIGHT } from './composer/composerSurfaceState'
import type { ComposerControls, ComposerProps } from './composer/types'
import { useIsMobile } from '../../app/shell/useIsMobile'
import { api } from '../data/api'
import { useQuery } from '../data/data'
import { usePushToTalk, ensureMicGrant } from '../data/pushToTalk'
import { MicCaptureChip } from './MicCaptureChip'
import {
  Composer as AssistantComposer,
  ComposerActions as AssistantComposerActions,
  ComposerAttachmentChip as AssistantComposerAttachmentChip,
  ComposerAttachments as AssistantComposerAttachments,
  ComposerContext as AssistantComposerContext,
  ComposerBar as AssistantComposerBar,
  ComposerSend as AssistantComposerSend,
  ComposerModelItem as AssistantComposerModelItem,
  ComposerModelTrigger as AssistantComposerModelTrigger,
  ComposerToolbar as AssistantComposerToolbar,
  ComposerVoiceButton as AssistantComposerVoiceButton,
} from '../vendor/assistant-ui/elements/composer'
import { ContextBreakdown } from '../vendor/assistant-ui/elements/context-breakdown'
import { DraftRestore } from '../vendor/assistant-ui/elements/draft-restore'
import { MobileComposer as AssistantMobileComposer } from '../vendor/assistant-ui/elements/mobile-composer'
import { ModelSelector } from '../vendor/assistant-ui/elements/model-selector.aui'
import { ComposerTriggerPopover } from '../vendor/assistant-ui/elements/composer-trigger-popover.aui'
import { activeMention, activeSlash } from './composer/editorState'
import { filterSlashCommands } from './composer/SlashMenu'
import { searchMentions, type MentionRow } from './composer/mentionSearch'
import './chat/chatPresentation.css'

const defaultControls: ComposerControls = { agent: true, model: true, approval: true, reasoning: false, attach: true, mic: true, optimize: true }
const primaryActions = {
  processing: { label: 'Processing…' },
  steer: { label: 'Steer — send into the running turn' },
  stop: { label: 'Stop' },
  sent: { label: 'Sent' },
  send: { label: 'Send message' },
  'send-disabled': { label: 'Send message' },
} as const

const gideonDirectiveFormatter: Unstable_DirectiveFormatter = {
    serialize: item => item.type === 'slash' ? item.id : `@${item.label}`,
    parse: text => [{ kind: 'text', text }],
};
const slashTriggerMatcher = (text: string, _char: string, cursor: number) => {
    const active = activeSlash(text, cursor);
    return active ? { query: active.query, offset: 0, endOffset: cursor } : null;
};
function AuiComposerTriggers({ value, onExecuteText, slash, mention, project, onMentionFile, onMentionKnowledge, renderEditor }: {
    value: string;
    onExecuteText: (text: string, caret: number) => void;
    slash: boolean;
    mention: boolean;
    project?: string;
    onMentionFile?: ComposerProps['onMentionFile'];
    onMentionKnowledge?: ComposerProps['onMentionKnowledge'];
    renderEditor: (onCursorChange: (position: number) => void, onTriggerKeyDown: (event: KeyboardEvent) => boolean, donorAria: Unstable_TriggerPopoverAriaProps) => ReactNode;
}) {
    const aui = useAui();
    const triggers = unstable_useTriggerPopoverTriggers();
    const donorAria = unstable_useTriggerPopoverAriaProps();
    const [cursor, setCursor] = useState(value.length);
    const [commands, setCommands] = useState<{ name: string; description: string }[]>([]);
    const [mentionAnswer, setMentionAnswer] = useState<{ query: string; rows: MentionRow[]; loading: boolean }>({ query: '', rows: [], loading: false });
    const active = activeMention(value, cursor);
    const mentionQuery = active?.query ?? '';
    const leading = active?.at === 0;
    useEffect(() => { if (aui.composer.getState().text !== value) aui.composer.setText(value); }, [aui, value]);
    useEffect(() => {
        for (const trigger of triggers.values()) trigger.resource.setCursorPosition(cursor);
    }, [triggers, cursor]);
    useEffect(() => {
        if (!slash) return;
        let current = true;
        api.slashCommands().then(result => { if (current) setCommands(result); }).catch(() => { if (current) setCommands([]); });
        return () => { current = false; };
    }, [slash]);
    useEffect(() => {
        if (!mention || mentionQuery.length < 2) {
            setMentionAnswer({ query: mentionQuery, rows: [], loading: false });
            return;
        }
        let current = true;
        setMentionAnswer({ query: mentionQuery, rows: [], loading: true });
        const timer = setTimeout(() => {
            searchMentions(mentionQuery, project, leading).then(rows => {
                if (current) setMentionAnswer({ query: mentionQuery, rows, loading: false });
            }).catch(() => { if (current) setMentionAnswer({ query: mentionQuery, rows: [], loading: false }); });
        }, 180);
        return () => { current = false; clearTimeout(timer); };
    }, [mention, mentionQuery, project, leading]);
    const slashAdapter = useMemo(() => ({
        categories: () => [], categoryItems: () => [],
        search: (query: string): Unstable_TriggerItem[] => filterSlashCommands(commands, query).map(command => ({
            id: command.name, type: 'slash', label: command.name, description: command.description,
        })),
    }), [commands]);
    const mentionRows = mentionAnswer.query === mentionQuery ? mentionAnswer.rows : [];
    const mentionAdapter = useMemo(() => ({
        categories: () => [], categoryItems: () => [],
        search: (): Unstable_TriggerItem[] => mentionRows.map(row => ({
            id: row.id, type: row.kind, label: row.name, description: row.sub,
        })),
    }), [mentionRows]);
    const handleKeyDown = (event: KeyboardEvent) => {
        for (const trigger of triggers.values()) {
            if (trigger.resource.handleKeyDown(event)) return true;
        }
        return false;
    };
    const execute = (item: Unstable_TriggerItem) => {
        const offset = item.type === 'slash' ? 0 : active?.at;
        if (offset === undefined) return;
        const directive = gideonDirectiveFormatter.serialize(item);
        const after = value.slice(cursor);
        const text = value.slice(0, offset) + directive + (after.startsWith(' ') ? after : ` ${after}`);
        onExecuteText(text, offset + directive.length + 1);
        if (item.type === 'file') onMentionFile?.({ path: item.id, name: item.label });
        if (item.type === 'knowledge') onMentionKnowledge?.({ id: item.id, name: item.label });
    };
    return <>
      {renderEditor(setCursor, handleKeyDown, donorAria)}
      {slash && <ComposerTriggerPopover char="/" matcher={slashTriggerMatcher} adapter={slashAdapter} aria-label={'Slash commands'}
        emptyItemsLabel={'No matching commands'} action={{ formatter: gideonDirectiveFormatter, onExecute: execute }}/>}
      {mention && <ComposerTriggerPopover char="@" adapter={mentionAdapter} isLoading={mentionAnswer.loading}
        aria-label={'Prompts, files and knowledge'} emptyItemsLabel={mentionQuery.length < 2 ? 'Type 2+ characters to search' : 'No matching files or knowledge'}
        action={{ formatter: gideonDirectiveFormatter, onExecute: execute }}/>}
    </>;
}
function measuredContextBreakdown(usage: ComposerProps['contextUsage']) {
  if (!usage) return null
  const { input_tokens: input, cache_creation_tokens: created, cache_read_tokens: read, context_window_tokens: limit } = usage
  if ([input, created, read, limit].some(value => typeof value !== 'number' || !Number.isFinite(value) || value < 0) || !limit) return null
  return {
    limit,
    segments: [
      { label: 'Input', tokens: input!, tint: 'bg-primary' },
      { label: 'Cache creation', tokens: created!, tint: 'bg-secondary' },
      { label: 'Cache read', tokens: read!, tint: 'bg-info' },
    ],
  }
}
function measuredContextRows(usage: ComposerProps['contextUsage']) {
  if (!usage) return []
  return [
    { label: 'Input', tokens: usage.input_tokens, tint: 'var(--color-primary)' },
    { label: 'Cache creation', tokens: usage.cache_creation_tokens, tint: 'var(--color-secondary)' },
    { label: 'Cache read', tokens: usage.cache_read_tokens, tint: 'var(--color-info)' },
  ].filter((row): row is { label: string; tokens: number; tint: string } =>
    typeof row.tokens === 'number' && Number.isFinite(row.tokens) && row.tokens >= 0)
}
function ModelControl({ data, agent, value, openSignal, onSelect, aui }: {
  data: ComposerProps['data']; agent: string; value: string; openSignal?: number; onSelect: (model: string) => void; aui?: boolean
}) {
  const menu = modelChoices(data, agent, value)
  const selector = useRef<HTMLDivElement>(null)
  useEffect(() => { if (openSignal && aui) selector.current?.querySelector('select')?.focus() }, [openSignal, aui])
  return <div ref={selector} className="flex items-center gap-1">
    {aui ? <ModelSelector models={menu.options.map(option => ({ id: option.value, name: option.label,
      description: menu.runtimeDefault && option.value === 'Auto' ? 'This runtime uses its own default model.' : option.hint }))}
      value={value || 'Auto'} onValueChange={onSelect} /> :
    <Popover portal width={280} openSignal={openSignal}
      trigger={(open, toggle) => <AssistantComposerModelTrigger model={menu.label} open={open}
        aria-label={`Model: ${menu.label}`} onClick={toggle} />}>
      {close => <div className="max-h-[320px] overflow-y-auto">
        {menu.options.map(option => <AssistantComposerModelItem key={option.value}
          entry={{ name: option.label, meta: option.hint ?? '' }} selected={(value || 'Auto') === option.value}
          onClick={() => { onSelect(option.value); close() }} />)}
        {menu.runtimeDefault && <p className="px-3 py-2 text-sm text-on-surface-low">This runtime uses its own default model.</p>}
      </div>}
    </Popover>}
  </div>
}

export function Composer(props: ComposerProps) {
  const { value, onChange, onSend, onStop, controls = defaultControls, data, selection, onSelect,
    onAttach, onOpenPrompts, plusMenuExtra, onOptimize, optimizing, onTranscribe, onMicError,
    handsFree, onHandsFreeSubmit, screenShare, naturalVoice } = props
  const surface = useComposerSurface({ ...props, onSend, attach: !!controls.attach })
  const contextBreakdown = measuredContextBreakdown(props.contextUsage)
  const contextRows = measuredContextRows(props.contextUsage)
  const reportedContextTotal = props.contextUsage?.total_input_tokens
  const contextTotal = typeof reportedContextTotal === 'number' && Number.isFinite(reportedContextTotal) && reportedContextTotal >= 0
    ? reportedContextTotal : contextBreakdown ? contextBreakdown.segments.reduce((sum, segment) => sum + segment.tokens, 0) : null
  const contextSources = props.attachments?.filter(attachment => (attachment.state ?? 'done') === 'done').map(attachment => attachment.name) ?? []
  const showContext = props.contextPct !== undefined || props.contextUsage !== undefined || contextSources.length > 0
  const mobile = useIsMobile()
  const reduced = useReducedMotion()
  const input = useRef<MarkdownInputHandle>(null)
  const files = useRef<HTMLInputElement>(null)
  const [listening, setListening] = useState(false)
  const [dictation, setDictation] = useState<{ heard: string; edited: string } | null>(null)
  const [heardTerm, setHeardTerm] = useState('')
  const [meantTerm, setMeantTerm] = useState('')
  const [savingCorrection, setSavingCorrection] = useState(false)
  const [savedDraft, setSavedDraft] = useState<{ key: string; draft: string; savedAt: string } | null>(null)
  const previousDraft = useRef<{ key?: string; value: string }>({ value: '' })
  const voiceAvailable = !!handsFree && !!onHandsFreeSubmit && !!onTranscribe
  const { data: sendOnEnter } = useQuery('chat:send-on-enter', () => api.dashboardConfig().then(config => config.send_on_enter), { persist: true })

  useEffect(() => {
    const key = props.draftKey && `gideon:composer-draft:${props.draftKey}`
    if (!key) { setSavedDraft(null); return }
    try {
      const stored = JSON.parse(localStorage.getItem(key) ?? 'null') as { draft?: string; savedAt?: string } | null
      setSavedDraft(stored?.draft && stored.draft !== value ? { key, draft: stored.draft, savedAt: stored.savedAt ?? '' } : null)
    } catch { setSavedDraft(null) }
  }, [props.draftKey])
  useEffect(() => {
    const key = props.draftKey && `gideon:composer-draft:${props.draftKey}`
    if (!key) return
    if (previousDraft.current.key !== key) { previousDraft.current = { key, value }; return }
    try {
      if (value.trim()) localStorage.setItem(key, JSON.stringify({ draft: value, savedAt: new Date().toLocaleString() }))
      else if (previousDraft.current.value.trim()) localStorage.removeItem(key)
    } catch { return }
    previousDraft.current = { key, value }
  }, [props.draftKey, value])

  useEffect(() => { if (!voiceAvailable) setListening(false) }, [voiceAvailable])
  const recorder = useMicRecorder(onTranscribe, text => setDictation({ heard: text, edited: text }), onMicError,
    voiceAvailable && handsFree ? {
      enabled: listening,
      confirmationPhrases: handsFree.confirmationPhrases,
      exitPhrases: handsFree.exitPhrases,
      muted: (handsFree.muteWhileSpeaking ?? true) && !!handsFree.speaking,
      onBuffer: onChange,
      onSubmit: text => { onChange(text); onHandsFreeSubmit?.(text) },
    } : undefined)
  usePushToTalk({
    capturing: recorder.state === 'recording',
    enabled: !!controls.mic && !!onTranscribe && !listening,
    onStart: () => { void ensureMicGrant().then(error => { if (error) onMicError?.(error); else recorder.toggle() }) },
    onStop: recorder.toggle,
  })

  const handsFreeLabel = !listening ? 'Hands-free voice' : recorder.muted
    ? 'Hands-free voice — paused while speaking'
    : `Hands-free voice — say “${handsFree?.confirmationPhrases[0] ?? 'go ahead'}” to send`
  const action = surface.action.kind
  const primary = primaryActions[action]
  const primaryClick = action === 'stop' ? onStop : surface.action.canSubmit ? surface.submit : undefined
  const active = surface.focused || surface.dropping

  const useDictation = async () => {
    if (!dictation?.edited.trim() || savingCorrection) return
    if (heardTerm.trim() || meantTerm.trim()) {
      if (!heardTerm.trim() || !meantTerm.trim()) {
        onMicError?.('Enter both the misheard word and its correction, or clear both fields.')
        return
      }
      setSavingCorrection(true)
      try { await api.lexiconAddCorrection(heardTerm.trim(), meantTerm.trim()) }
      catch (error) { onMicError?.(`Could not learn that correction: ${(error as Error).message}`); setSavingCorrection(false); return }
      setSavingCorrection(false)
    }
    input.current?.insertAtCaret(dictation.edited.trim())
    setDictation(null); setHeardTerm(''); setMeantTerm('')
  }
  const renderEditor = (onCursorChange?: (position: number) => void, onTriggerKeyDown?: (event: KeyboardEvent) => boolean, donorAria?: Unstable_TriggerPopoverAriaProps) => <MarkdownInput ref={input} value={value} onChange={onChange} onSend={surface.submit} canSend={surface.draftReady}
    placeholder={props.placeholder ?? 'Ask anything'} minHeight={surface.height} maxHeight={COMPOSER_HEIGHT.max}
    onFocusChange={surface.focus} onOptimize={optimizing ? undefined : onOptimize} history={props.history}
    onMentionFile={props.onMentionFile} onMentionKnowledge={props.onMentionKnowledge} mentionProject={props.mentionProject}
    slashCommands={!!controls.slash} onLargePaste={props.onLargePaste} mobile={mobile} sendOnEnter={sendOnEnter}
    donorTriggers={props.auiModelSelector !== undefined} onCursorChange={onCursorChange} onTriggerKeyDown={onTriggerKeyDown} donorAria={donorAria} />
  const editor = props.auiModelSelector !== undefined ? <ComposerPrimitive.Unstable_TriggerPopoverRoot>
    <AuiComposerTriggers value={value} onExecuteText={(text, caret) => input.current?.applyDonorText(text, caret)} slash={!!controls.slash} mention={!!(props.onMentionFile || props.onMentionKnowledge)} project={props.mentionProject}
      onMentionFile={props.onMentionFile} onMentionKnowledge={props.onMentionKnowledge} renderEditor={renderEditor} />
  </ComposerPrimitive.Unstable_TriggerPopoverRoot> : renderEditor()

  return <AssistantComposer className="relative w-full min-w-0 max-w-none" style={{ maxWidth: 'var(--content-width)' }} data-gideon-composer="true">
    <motion.div layout animate={{ y: reduced ? 0 : active ? -expr(3, 0.4) : 0 }} transition={reduced ? { duration: 0 } : physics.snappy}
      {...surface.dropBindings}
      className="relative isolate"
      data-composer-dropzone="true" data-focused={surface.focused || undefined} data-active={active || undefined}>
      <AssistantComposerBar dragActive={surface.dropping}
        className="gideon-composer-panel relative flex flex-col gap-3 overflow-visible border bg-surface-container px-3 pb-2 pt-4">
      <div role="separator" aria-label="Resize message input" aria-orientation="horizontal" aria-valuenow={surface.height}
        aria-valuemin={COMPOSER_HEIGHT.min} aria-valuemax={COMPOSER_HEIGHT.max} tabIndex={0} title="Drag to resize"
        {...surface.resizeBindings}
        className="group absolute -top-2 left-1/2 z-10 flex h-5 w-14 -translate-x-1/2 cursor-ns-resize items-center justify-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
        <span className="h-0.5 w-7 rounded-full bg-outline-variant transition-colors group-hover:bg-primary" />
      </div>
      {mobile ? <AssistantMobileComposer editor={editor} value={value} keyboardOpen={surface.focused} keyboardHint={null}
        running={action === 'stop'} actions={[]} embedded className="max-w-none"
        style={{ background: 'transparent', border: 0, borderRadius: 0, padding: 0, maxWidth: 'none' }}
        onSend={surface.action.canSubmit ? surface.submit : undefined} onStop={action === 'stop' ? onStop : undefined} /> : editor}
      {savedDraft && <DraftRestore draft={savedDraft.draft} savedAt={savedDraft.savedAt}
        onRestore={() => { onChange(savedDraft.draft); setSavedDraft(null) }}
        onDiscard={() => { try { localStorage.removeItem(savedDraft.key) } catch { setSavedDraft(null) }; setSavedDraft(null) }} />}
      {!!props.attachments?.length && <AssistantComposerAttachments aria-label="Attached files">
        {props.attachments.map(attachment => <div key={attachment.id} className="flex items-center gap-1">
          <AssistantComposerAttachmentChip
            attachment={{ name: attachment.name, meta: attachment.meta ?? 'Attached', state: attachment.state ?? 'done', kind: attachment.kind, progress: attachment.progress }}
            onRemove={props.onRemoveAttachment ? () => props.onRemoveAttachment?.(attachment.id) : undefined} />
          {props.onOpenAttachment && (attachment.state ?? 'done') === 'done' && <button type="button" aria-label={`Open ${attachment.name}`}
            onClick={() => props.onOpenAttachment?.(attachment.id)} className="rounded-lg px-2 py-1 text-xs text-primary hover:bg-primary/10">Open</button>}
        </div>)}
      </AssistantComposerAttachments>}
      {contextBreakdown && <ContextBreakdown {...contextBreakdown} />}
      {dictation && !listening && <div role="group" aria-label="Review speech transcription" className="rounded-lg border border-outline-variant/50 bg-surface-high p-3">
        <div className="mb-2 flex items-center justify-between gap-2"><span data-type="label-s">Review what Gideon heard</span>
          <button type="button" aria-label="Discard transcription" onClick={() => setDictation(null)} className="rounded p-1 text-on-surface-low hover:text-on-surface"><X size={14} /></button></div>
        <textarea aria-label="Edit transcription" value={dictation.edited} onChange={event => setDictation({ ...dictation, edited: event.target.value })}
          rows={3} className="w-full resize-y rounded-md border border-outline-variant/50 bg-surface-container p-2 text-on-surface outline-none focus:border-primary" />
        <p data-type="caption" className="mt-1 text-on-surface-low">Fix the transcript before adding it to your message. To teach a spelling, enter one misheard word and its replacement.</p>
        <div className="mt-2 flex flex-wrap gap-2">
          <input aria-label="Misheard word" placeholder="Gideon heard…" value={heardTerm} onChange={event => setHeardTerm(event.target.value)} className="min-w-0 flex-1 rounded-md border border-outline-variant/50 bg-surface-container px-2 py-1 text-on-surface" />
          <input aria-label="Correct spelling" placeholder="You meant…" value={meantTerm} onChange={event => setMeantTerm(event.target.value)} className="min-w-0 flex-1 rounded-md border border-outline-variant/50 bg-surface-container px-2 py-1 text-on-surface" />
          <button type="button" onClick={() => { void useDictation() }} disabled={!dictation.edited.trim() || savingCorrection} className="rounded-md bg-primary px-3 py-1 text-on-primary disabled:opacity-40">{savingCorrection ? 'Saving…' : 'Use text'}</button>
        </div>
      </div>}
      <AssistantComposerToolbar className="gideon-composer-toolbar flex flex-wrap items-end justify-between gap-x-3 gap-y-2 border-t pt-2">
        <div className="gideon-composer-options flex min-w-0 flex-wrap items-center gap-1" aria-label="Message options">
          {controls.attach && <PlusMenu onAttach={() => files.current?.click()} onOpenPrompts={onOpenPrompts} extra={plusMenuExtra} />}
          {controls.agent && <AgentPill data={data} value={selection?.agent ?? ''} openSignal={props.openAgentSignal} onSelect={agent => onSelect?.({ agent })} />}
          {controls.model && props.auiModelSelector !== false && <ModelControl data={data} agent={selection?.agent ?? ''} value={selection?.model ?? ''} aui={props.auiModelSelector}
            openSignal={props.openModelSignal} onSelect={model => onSelect?.({ model })} />}
          {showContext && <AssistantComposerContext measured={{ percent: props.contextPct ?? null,
            usedTokens: contextTotal,
            windowTokens: props.contextUsage?.context_window_tokens ?? null,
            breakdown: contextRows, breakdownComplete: !!contextBreakdown, sources: contextSources }} />}
          {controls.approval && <ApprovalPill value={selection?.approval ?? 'normal'} onSelect={approval => onSelect?.({ approval })} />}
          {controls.reasoning && <ReasoningPill value={selection?.reasoning ?? ''} efforts={effortsForAgent(data, selection?.agent ?? '')}
            openSignal={props.openReasoningSignal} onSelect={reasoning => onSelect?.({ reasoning })} />}
          {naturalVoice && <NaturalVoicePill {...naturalVoice} />}
        </div>
        <AssistantComposerActions className="gideon-composer-actions ml-auto flex shrink-0 items-center gap-1" aria-label="Message actions">
          {controls.optimize && onOptimize && <IconButton icon={Sparkles} label="Optimize prompt (⌘↵)"
            disabledReason="Type something first" active={optimizing} size={40} disabled={!optimizing && !surface.draftReady}
            loading={optimizing} onClick={onOptimize} />}
          {recorder.state === 'recording' && <MicCaptureChip onStop={recorder.toggle} />}
          {controls.mic && onTranscribe && !listening && <AssistantComposerVoiceButton active={recorder.state === 'recording'}
            aria-label={recorder.state === 'recording' ? 'Stop recording' : 'Voice input'}
            aria-busy={recorder.state === 'transcribing'} onClick={recorder.toggle} />}
          {screenShare?.available && <IconButton icon={MonitorUp} label={screenShare.sharing ? 'Stop sharing screen' : 'Share screen'}
            active={screenShare.sharing} size={40} disabled={!!screenShare.disabledReason} disabledReason={screenShare.disabledReason}
            onClick={screenShare.disabledReason ? undefined : screenShare.onToggle} />}
          {controls.mic && voiceAvailable && <IconButton icon={Ear} label={handsFreeLabel} active={listening} size={40}
            disabled={!listening && recorder.state !== 'idle'} disabledReason="Finish the current recording first"
            onClick={() => setListening(previous => !previous)} />}
          {!mobile && <AssistantComposerSend streaming={action === 'stop'} idle={action === 'send-disabled'}
            aria-label={primary.label} aria-disabled={action === 'send-disabled'} aria-busy={action === 'processing'}
            disabled={action === 'send-disabled' || action === 'processing' || action === 'sent'}
            onClick={primaryClick} className="gideon-composer-primary size-10" />}
        </AssistantComposerActions>
      </AssistantComposerToolbar>
      <input ref={files} type="file" multiple hidden aria-label="Attach message files" onChange={event => {
        const selected = event.currentTarget.files
        if (selected && onAttach) onAttach(Array.from(selected))
        event.currentTarget.value = ''
      }} />
      <AnimatePresence>{surface.dropping && <motion.div key="file-drop" role="status" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        transition={{ duration: reduced ? 0 : 0.12 }} className="pointer-events-none absolute inset-1 z-20 flex items-center justify-center gap-3 rounded-xl border-2 border-dashed border-primary bg-surface-container/95 text-primary">
        <Paperclip size={22} aria-hidden /><span data-type="label-m">Drop files to attach</span>
      </motion.div>}</AnimatePresence>
      </AssistantComposerBar>
    </motion.div>
  </AssistantComposer>
}
