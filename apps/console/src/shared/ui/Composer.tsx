import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { ArrowUp, Square, CornerDownLeft, Sparkles, Mic, Ear, Paperclip, Check, MonitorUp, X } from 'lucide-react'
import { IconButton } from './IconButton'
import { physics, expr, useReducedMotion } from '../theme/motion'
import { AgentPill, ModelPill, ApprovalPill, ReasoningPill, NaturalVoicePill, effortsForAgent, PlusMenu } from './composer/controls'
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
import './chat/chatPresentation.css'

const defaultControls: ComposerControls = { agent: true, model: true, approval: true, reasoning: false, attach: true, mic: true, optimize: true }
const primaryActions = {
  processing: { icon: ArrowUp, label: 'Processing…' },
  steer: { icon: CornerDownLeft, label: 'Steer — send into the running turn' },
  stop: { icon: Square, label: 'Stop' },
  sent: { icon: Check, label: 'Sent' },
  send: { icon: ArrowUp, label: 'Send message' },
  'send-disabled': { icon: ArrowUp, label: 'Send message' },
} as const

export function Composer(props: ComposerProps) {
  const { value, onChange, onSend, onStop, controls = defaultControls, data, selection, onSelect,
    onAttach, onOpenPrompts, plusMenuExtra, onOptimize, optimizing, onTranscribe, onMicError,
    handsFree, onHandsFreeSubmit, screenShare, naturalVoice } = props
  const surface = useComposerSurface({ ...props, onSend, attach: !!controls.attach })
  const mobile = useIsMobile()
  const reduced = useReducedMotion()
  const input = useRef<MarkdownInputHandle>(null)
  const files = useRef<HTMLInputElement>(null)
  const [listening, setListening] = useState(false)
  const [dictation, setDictation] = useState<{ heard: string; edited: string } | null>(null)
  const [heardTerm, setHeardTerm] = useState('')
  const [meantTerm, setMeantTerm] = useState('')
  const [savingCorrection, setSavingCorrection] = useState(false)
  const voiceAvailable = !!handsFree && !!onHandsFreeSubmit && !!onTranscribe
  const { data: sendOnEnter } = useQuery('chat:send-on-enter', () => api.dashboardConfig().then(config => config.send_on_enter), { persist: true })

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

  return <div className="relative w-full min-w-0" style={{ maxWidth: 'var(--content-width)' }} data-gideon-composer="true">
    <motion.div layout animate={{ y: reduced ? 0 : active ? -expr(3, 0.4) : 0 }} transition={reduced ? { duration: 0 } : physics.snappy}
      {...surface.dropBindings}
      className="gideon-composer-panel relative isolate flex flex-col gap-3 overflow-visible border bg-surface-container px-3 pb-2 pt-4"
      data-composer-dropzone="true" data-focused={surface.focused || undefined} data-active={active || undefined}>
      <div role="separator" aria-label="Resize message input" aria-orientation="horizontal" aria-valuenow={surface.height}
        aria-valuemin={COMPOSER_HEIGHT.min} aria-valuemax={COMPOSER_HEIGHT.max} tabIndex={0} title="Drag to resize"
        {...surface.resizeBindings}
        className="group absolute -top-2 left-1/2 z-10 flex h-5 w-14 -translate-x-1/2 cursor-ns-resize items-center justify-center rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
        <span className="h-0.5 w-7 rounded-full bg-outline-variant transition-colors group-hover:bg-primary" />
      </div>
      <MarkdownInput ref={input} value={value} onChange={onChange} onSend={surface.submit} canSend={surface.draftReady}
        placeholder={props.placeholder ?? 'Ask anything'} minHeight={surface.height} maxHeight={COMPOSER_HEIGHT.max}
        onFocusChange={surface.focus} onOptimize={optimizing ? undefined : onOptimize} history={props.history}
        onMentionFile={props.onMentionFile} onMentionKnowledge={props.onMentionKnowledge} mentionProject={props.mentionProject}
        slashCommands={!!controls.slash} onLargePaste={props.onLargePaste} mobile={mobile} sendOnEnter={sendOnEnter} />
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
      <div className="gideon-composer-toolbar flex flex-wrap items-end justify-between gap-x-3 gap-y-2 border-t pt-2">
        <div className="gideon-composer-options flex min-w-0 flex-wrap items-center gap-1" aria-label="Message options">
          {controls.attach && <PlusMenu onAttach={() => files.current?.click()} onOpenPrompts={onOpenPrompts} extra={plusMenuExtra} />}
          {controls.agent && <AgentPill data={data} value={selection?.agent ?? ''} openSignal={props.openAgentSignal} onSelect={agent => onSelect?.({ agent })} />}
          {controls.model && <ModelPill data={data} agent={selection?.agent ?? ''} value={selection?.model ?? ''} contextPct={props.contextPct}
            openSignal={props.openModelSignal} onSelect={model => onSelect?.({ model })} />}
          {controls.approval && <ApprovalPill value={selection?.approval ?? 'normal'} onSelect={approval => onSelect?.({ approval })} />}
          {controls.reasoning && <ReasoningPill value={selection?.reasoning ?? ''} efforts={effortsForAgent(data, selection?.agent ?? '')}
            openSignal={props.openReasoningSignal} onSelect={reasoning => onSelect?.({ reasoning })} />}
          {naturalVoice && <NaturalVoicePill {...naturalVoice} />}
        </div>
        <div className="gideon-composer-actions ml-auto flex shrink-0 items-center gap-1" aria-label="Message actions">
          {controls.optimize && onOptimize && <IconButton icon={Sparkles} label="Optimize prompt (⌘↵)"
            disabledReason="Type something first" active={optimizing} size={40} disabled={!optimizing && !surface.draftReady}
            loading={optimizing} onClick={onOptimize} />}
          {recorder.state === 'recording' && <MicCaptureChip onStop={recorder.toggle} />}
          {controls.mic && onTranscribe && !listening && <IconButton icon={Mic}
            label={recorder.state === 'recording' ? 'Stop recording' : 'Voice input'} active={recorder.state !== 'idle'}
            size={40} loading={recorder.state === 'transcribing'} onClick={recorder.toggle} />}
          {screenShare?.available && <IconButton icon={MonitorUp} label={screenShare.sharing ? 'Stop sharing screen' : 'Share screen'}
            active={screenShare.sharing} size={40} disabled={!!screenShare.disabledReason} disabledReason={screenShare.disabledReason}
            onClick={screenShare.disabledReason ? undefined : screenShare.onToggle} />}
          {controls.mic && voiceAvailable && <IconButton icon={Ear} label={handsFreeLabel} active={listening} size={40}
            disabled={!listening && recorder.state !== 'idle'} disabledReason="Finish the current recording first"
            onClick={() => setListening(previous => !previous)} />}
          <IconButton icon={primary.icon} label={primary.label} size={40} className="gideon-composer-primary" filled={action !== 'send-disabled'}
            disabled={action === 'send-disabled'} disabledReason="Type a bit more first" loading={action === 'processing'}
            onClick={primaryClick} iconKey={action === 'sent' ? 'sent' : action === 'send' || action === 'send-disabled' ? 'send' : action}
            bloom={action === 'sent'} />
        </div>
      </div>
      <input ref={files} type="file" multiple hidden aria-label="Attach message files" onChange={event => {
        const selected = event.currentTarget.files
        if (selected && onAttach) onAttach(Array.from(selected))
        event.currentTarget.value = ''
      }} />
      <AnimatePresence>{surface.dropping && <motion.div key="file-drop" role="status" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
        transition={{ duration: reduced ? 0 : 0.12 }} className="pointer-events-none absolute inset-1 z-20 flex items-center justify-center gap-3 rounded-xl border-2 border-dashed border-primary bg-surface-container/95 text-primary">
        <Paperclip size={22} aria-hidden /><span data-type="label-m">Drop files to attach</span>
      </motion.div>}</AnimatePresence>
    </motion.div>
  </div>
}
