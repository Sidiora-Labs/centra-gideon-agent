import { createRef, useCallback, useEffect, useMemo, useReducer, useState, type ReactNode } from 'react'
import { motion } from 'framer-motion'
import { ArrowRight, User, Boxes, Rocket, Sparkles, Loader2, Check, Compass, Inbox, Waves, PanelLeft, FolderInput, RefreshCw, type LucideIcon } from 'lucide-react'
import { GideonMark } from '../../shared/ui/GideonMark'
import { DotGlow } from '../../shared/ui/DotGlow'
import { LoadingStatus } from '../../shared/ui/ListScaffold'
import { Button } from '../../shared/ui/Button'
import { TextLink } from '../../shared/ui/TextLink'
import { Toggle } from '../../shared/ui/Toggle'
import { ScalarControl } from '../../shared/ui/TokenControls'
import { TOKENS, type ScalarToken } from '../../shared/theme/tokenRegistry'
import { spring } from '../../shared/theme/motion'
import { withWeight } from '../../shared/theme/fontWeight'
import { unavailableWhen } from '../../shared/ui/unavailable'
import { useIdentity, firstNameOf, DEFAULT_USER_NAME, suggestHandle, USERNAME_MAX_LEN } from './identity'
import { setNavMode } from './navDisclosure'
import { APP_NAME } from './config'
import { reportActionFailure } from './reportingWrite'
import { api, type OnboardingStatePatch } from '../../shared/data/api'
import { StepRow, type StepState } from '../../features/onboarding/StepStack'
import { EssentialsStep } from '../../features/onboarding/EssentialsStep'
import { ImportStep } from '../../features/onboarding/ImportStep'
import { TryOneStep } from '../../features/onboarding/TryOneStep'
import { setOnboardingExit } from '../../features/onboarding/exitTo'
import { requestProductTour } from '../../features/onboarding/tourLaunch'
import { ORDER, TITLES, initialSetup, setupReducer, type StepId } from './onboardingState'

const BOUNCINESS = TOKENS.find((token) => token.varName === '--bounciness') as ScalarToken | undefined
const steps: Record<StepId, { icon: LucideIcon; subtitle: string }> = {
  name: { icon: User, subtitle: 'How the system addresses you. Saved on the server, so it follows you across devices.' },
  import: { icon: FolderInput, subtitle: 'Already use another local agent tool? Bring its instructions, MCP servers and skills across.' },
  essentials: { icon: Boxes, subtitle: 'Install what the agent needs to work. A model provider is required; the rest are optional.' },
  try: { icon: Rocket, subtitle: 'Watch it actually do something. Each one runs for real — and none of them is required.' },
  ready: { icon: Sparkles, subtitle: '' },
}
export function Onboarding() {
  const { setName } = useIdentity()
  const [state, dispatch] = useReducer(setupReducer, initialSetup)
  const [handleDraft, setHandleDraft] = useState<string | null>(null)
  const handle = handleDraft ?? suggestHandle(state.draft)
  const rows = useMemo(() => Object.fromEntries(ORDER.map((id) => [id, createRef<HTMLLIElement>()])) as Record<StepId, React.RefObject<HTMLLIElement | null>>, [])
  const progress = useCallback((patch: OnboardingStatePatch) => { void api.saveOnboardingState(patch).catch(() => {}) }, [])
  useEffect(() => {
    let active = true
    const load = async () => {
      let readiness
      try { readiness = await api.onboarding() }
      catch { readiness = { needs_model: true, has_model_provider: false, has_chat_binding: false } }
      if (active) dispatch({ type: 'loaded', value: readiness })
    }
    void load()
    return () => { active = false }
  }, [])
  const commitName = () => {
    if (!state.draft.trim()) return
    dispatch({ type: 'name' })
    if (state.resume === 'essentials') progress({ step: 'essentials' })
    if (state.resume === 'try') progress({ step: 'first_success' })
  }
  const advance = (type: 'import' | 'essentials' | 'try', summary: string) => {
    dispatch({ type, summary })
    if (type === 'import') progress({ step: 'essentials' })
    if (type === 'essentials') progress({ step: 'first_success' })
  }
  function finish() {
    progress({ step: 'done' })
    setNavMode(state.showEverything ? 'expert' : 'starter')
    if (state.name) void setName(state.name, handleDraft ?? suggestHandle(state.name))
    else void setName(DEFAULT_USER_NAME)
  }
  function exitTo(path: string) {
    setOnboardingExit(path)
    finish()
  }
  const tour = () => { requestProductTour(); finish() }
  const summaries: Partial<Record<StepId, string>> = { name: state.name, import: state.imported, essentials: state.model, try: state.tried }
  const content: Record<StepId, ReactNode> = {
    name: <NameStep handle={handle} changeHandle={setHandleDraft} value={state.draft} change={(value) => dispatch({ type: 'draft', value })} submit={commitName} />,
    import: <ImportStep onDone={(summary) => advance('import', summary)} onSkip={() => advance('import', 'Skipped')} />,
    essentials: state.readiness ? <EssentialsStep readiness={state.readiness} onProgress={progress} onDone={(summary) => advance('essentials', summary)} onSkip={() => advance('essentials', 'Set up later')} />
      : <div role="status" aria-busy="true" className="flex items-center gap-s py-s"><LoadingStatus what="what's already set up" /><Loader2 size={18} className="animate-spin text-on-surface-low" aria-hidden="true" /></div>,
    try: <TryOneStep onProgress={progress} onDone={(summary) => advance('try', summary)} onSkip={() => advance('try', 'Skipped')} onExitTo={exitTo} />,
    ready: <ReadyScreen name={state.name} model={state.model} tried={state.tried} showEverything={state.showEverything}
      setDisclosure={(value) => dispatch({ type: 'disclosure', value })} finish={finish} tour={tour} exitTo={exitTo} />,
  }
  return <div data-onboarding-scroll className="fixed inset-0 z-[var(--z-modal)] h-dvh min-h-0 overflow-y-auto overscroll-contain bg-canvas">
    <DotGlow intensity={1.15} composerRef={rows[state.step]} />
    <main className="relative mx-auto flex min-h-full w-full max-w-3xl flex-col justify-start gap-2xl px-l py-3xl">
      <header className="flex items-center gap-l rounded-2xl border border-outline-variant/40 bg-surface/80 p-l">
        <GideonMark size={56} animated blob />
        <div className="min-w-0"><h1 data-type="headline-m" className="text-on-surface">Welcome to {APP_NAME}</h1>
          <p className="mt-s text-on-surface-low">Your self-hosted personal agent. A few moments to get set up.</p></div>
      </header>
      {/* The live region adds progress numbers to the focused step heading. */}
      <p role="status" aria-live="polite" className="sr-only">{`Step ${ORDER.indexOf(state.step) + 1} of ${ORDER.length}: ${TITLES[state.step]}`}</p>
      <ol className="flex w-full list-none flex-col gap-2 p-0">
        {ORDER.map((id, index) => {
          const position: StepState = id === state.step ? 'active' : index < ORDER.indexOf(state.step) ? 'done' : 'upcoming'
          return <StepRow key={id} ref={rows[id]} index={index} icon={steps[id].icon} title={TITLES[id]}
            subtitle={id === 'ready' ? `You're ready, ${firstNameOf(state.name)}.` : steps[id].subtitle}
            state={position} doneSummary={summaries[id] || undefined} onActivate={id === 'ready' ? undefined : () => dispatch({ type: 'visit', step: id })}>
            {content[id]}
          </StepRow>
        })}
      </ol>
      {state.step !== 'ready' && <div className="flex justify-center"><TextLink size="sm" ink="emphasis" onClick={finish}>
        {state.step === 'name' ? `Skip setup — start as ${DEFAULT_USER_NAME}, rename yourself in Settings` : 'Skip setup and go to the dashboard'}
      </TextLink></div>}
    </main>
  </div>
}
export function NameStep({ value, change, handle, changeHandle, submit }: {
  value: string; change: (value: string) => void; handle: string; changeHandle: (value: string) => void; submit: () => void
}) {
  return <form className="flex flex-col gap-m" onSubmit={(event) => { event.preventDefault(); submit() }}>
    <div className="flex items-center gap-s rounded-xl border border-outline-variant bg-surface-high p-s focus-within:ring-2 focus-within:ring-inset focus-within:ring-primary">
      <input autoFocus aria-label="Your name" placeholder="Your name" value={value} onChange={(event) => change(event.target.value)}
        className="min-w-0 flex-1 bg-transparent px-m py-s text-on-surface outline-none placeholder:text-on-surface-low" />
      <motion.button type="submit" aria-label="Continue" {...unavailableWhen(!value.trim(), 'Enter your name first')} whileTap={{ scale: 0.96 }} transition={spring.spatialFast}
        className="inline-flex size-11 shrink-0 items-center justify-center rounded-lg aria-disabled:cursor-not-allowed aria-disabled:opacity-40" style={{ background: 'var(--color-primary)', color: 'var(--color-on-primary)' }}><ArrowRight size={18} /></motion.button>
    </div>
    <label className="flex flex-col gap-s">
      <span>Attribution handle (optional)</span>
      <input aria-label="Attribution handle (optional)" aria-describedby="attribution-hint" placeholder="your-handle" value={handle} maxLength={USERNAME_MAX_LEN}
        onChange={(event) => changeHandle(event.target.value.slice(0, USERNAME_MAX_LEN))}
        className="rounded-lg border border-outline-variant bg-surface-high px-m py-s text-on-surface" />
    </label>
    <p id="attribution-hint" className="text-on-surface-low">Labels tasks and comments you create. Leave empty for no attribution; you can change it in Settings.</p>
  </form>
}
function ReadyScreen({ name, model, tried, showEverything, setDisclosure, finish, tour, exitTo }: {
  name: string; model: string; tried: string; showEverything: boolean; setDisclosure: (value: boolean) => void; finish: () => void; tour: () => void; exitTo: (path: string) => void
}) {
  const [autonomy, setAutonomy] = useState<{ autoUpdate: boolean; registryEnabled: boolean } | 'failed' | null>(null)
  useEffect(() => {
    let active = true
    void api.gideonConfig().then((config) => {
      const apps = (config.apps ?? {}) as { registry_source_enabled?: boolean }
      if (active) setAutonomy({ autoUpdate: config.auto_update !== false, registryEnabled: apps.registry_source_enabled !== false })
    }).catch(() => { if (active) setAutonomy('failed') })
    return () => { active = false }
  }, [])
  const updateAutomatically = (value: boolean) => {
    setAutonomy((current) => current && current !== 'failed' ? { ...current, autoUpdate: value } : current)
    void api.setAutoUpdate(value).catch(reportActionFailure(`${value ? 'enable' : 'disable'} automatic updates`))
  }
  const chatReady = Boolean(model && model !== 'Set up later'), trialDone = Boolean(tried && tried !== 'Skipped')
  const recap = [
    { ok: true, text: `Hello, ${firstNameOf(name)}` },
    { ok: chatReady, text: chatReady ? `Chat model: ${model}` : 'Chat model — set up later in Settings' },
    { ok: trialDone, text: trialDone ? `First success: ${tried}` : 'Nothing tried yet — the cards are in Discover' },
  ]
  const pointers: Array<{ icon: LucideIcon; title: string; body: string; control: ReactNode }> = [
    { icon: Inbox, title: 'Work comes back to you in the Inbox', body: 'Approvals, reminders and finished runs queue up there instead of chasing you across the app.',
      control: <TextLink size="sm" ink="emphasis" onClick={() => exitTo('inbox')}>Open the Inbox instead</TextLink> },
    { icon: Waves, title: 'How much the interface moves is a dial', body: 'Every animation scales with it — all the way down to none. This is the real control from Settings → Design.', control: BOUNCINESS && <ScalarControl token={BOUNCINESS} /> },
    { icon: PanelLeft, title: 'The sidebar starts short and grows', body: showEverything ? 'It will list every destination from the start. You can shorten it again in Settings → Design.' : 'Five essentials now; any other surface joins it the first time you open one. Nothing is locked away.',
      control: <SettingToggle value={showEverything} change={setDisclosure} label="Show every surface" /> },
    { icon: RefreshCw, title: 'It keeps itself current on its own', body: 'When a new version ships, it installs and restarts unattended. This is the real switch from Settings → Updates.',
      control: <div className="flex flex-col gap-s">
        {autonomy === 'failed' ? <TextLink size="sm" ink="emphasis" onClick={() => exitTo('settings/updates')}>Manage updates in Settings</TextLink> : autonomy ? <SettingToggle value={autonomy.autoUpdate} change={updateAutomatically} label="Update automatically" /> : null}
        {autonomy && autonomy !== 'failed' && autonomy.registryEnabled && <p className="text-on-surface-low text-[0.8125rem]">App discovery uses the sources configured for this gateway; installing anything still runs the security scanner. <TextLink size="sm" ink="emphasis" onClick={() => exitTo('apps')}>Review Store sources</TextLink></p>}
      </div> },
  ]
  return <div className="flex flex-col gap-l">
    <div className="grid gap-s rounded-xl border border-outline-variant/40 p-m">{recap.map(({ ok, text }) => <div key={text} className="flex items-center gap-s text-[0.8125rem]">
      <span className="grid size-6 place-items-center rounded-lg" style={{ background: ok ? 'var(--color-success)' : 'var(--color-surface-high)', color: ok ? 'var(--color-on-primary)' : 'var(--color-on-surface-low)' }}><Check size={13} /></span>
      <span className="text-on-surface-var">{text}</span>
    </div>)}</div>
    <p data-type="label-s" className="text-on-surface-low">Four things to know</p>
    <div className="grid gap-m">{pointers.map(({ icon: Icon, title, body, control }) => <section key={title} className="flex items-start gap-m rounded-xl border border-outline-variant/40 bg-surface-high p-m">
      <Icon size={19} className="mt-1 shrink-0 text-primary" aria-hidden="true" />
      <div className="min-w-0 flex-1"><h3 className="text-on-surface text-[0.8125rem]" style={withWeight({}, 600)}>{title}</h3><p className="mt-s text-on-surface-low text-[0.8125rem]">{body}</p><div className="mt-m">{control}</div></div>
    </section>)}</div>
    <div className="flex flex-wrap gap-s"><Button size="lg" onClick={finish}>Start using {APP_NAME} <ArrowRight size={17} /></Button>
      <Button variant="secondary" size="lg" onClick={tour}><Compass size={17} /> Take the quick tour</Button></div>
  </div>
}
function SettingToggle({ value, change, label }: { value: boolean; change: (value: boolean) => void; label: string }) {
  return <div className="flex items-center gap-s"><Toggle on={value} onChange={change} label={label} /><span className="text-on-surface-var text-[0.8125rem]">{label}</span></div>
}
